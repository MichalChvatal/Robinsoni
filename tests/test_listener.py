import socket
from copy import deepcopy
from ipaddress import ip_address

import pytest
from mr_quality_monitor.config import ReceiverConfig
from mr_quality_monitor.database.models import AnalysisJob, DicomInstance
from mr_quality_monitor.dicom.listener import Listener
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    MRImageStorage,
    SecondaryCaptureImageStorage,
)
from pynetdicom import AE
from pynetdicom.sop_class import Verification
from sqlalchemy import func, select


@pytest.fixture
def listener(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    receiver = Listener(ReceiverConfig(storage_root=tmp_path, port=port, minimum_free_bytes=0))
    receiver.start()
    yield receiver
    receiver.stop()


def connect(listener, calling="MR_TEST", called="MR_QUALITY", syntaxes=None):
    ae = AE(ae_title=calling)
    ae.add_requested_context(Verification)
    for sop in (MRImageStorage, SecondaryCaptureImageStorage):
        ae.add_requested_context(sop, syntaxes or [ExplicitVRLittleEndian, ImplicitVRLittleEndian])
    return ae.associate("127.0.0.1", listener.config.port, ae_title=called)


def test_echo_store_duplicate_conflict_and_restart(listener, image):
    assoc = connect(listener)
    assert assoc.is_established
    try:
        assert assoc.send_c_echo().Status == 0
        assert assoc.send_c_store(image).Status == 0
        assert assoc.send_c_store(image).Status == 0
        changed = deepcopy(image)
        changed.PixelData = b"\x03\x00" * 16
        assert assoc.send_c_store(changed).Status == 0xC211
    finally:
        assoc.release()
    with listener.storage.sessions() as session:
        assert session.scalar(select(func.count()).select_from(DicomInstance)) == 1
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 1
    listener.stop()
    listener.start()
    assoc = connect(listener)
    try:
        assert assoc.send_c_store(image).Status == 0
    finally:
        assoc.release()


@pytest.mark.parametrize("sop", [MRImageStorage, SecondaryCaptureImageStorage])
@pytest.mark.parametrize("syntax", [ExplicitVRLittleEndian, ImplicitVRLittleEndian])
def test_supported_classes_and_syntaxes(listener, image, sop, syntax):
    image.SOPClassUID = sop
    image.file_meta.TransferSyntaxUID = syntax
    assoc = connect(listener, syntaxes=[syntax])
    try:
        assert any(
            context.abstract_syntax == sop and context.transfer_syntax == [syntax]
            for context in assoc.accepted_contexts
        )
        assert assoc.send_c_store(image).Status == 0
    finally:
        assoc.release()


@pytest.mark.parametrize("calling,called", [("UNEXPECTED", "MR_QUALITY"), ("MR_TEST", "WRONG")])
def test_wrong_ae_is_rejected(listener, calling, called):
    assoc = connect(listener, calling, called)
    assert not assoc.is_established


def test_wrong_peer_ip_is_rejected(listener):
    config = listener.config.model_copy(update={"allowed_peer_ip": ip_address("192.0.2.1")})
    listener.stop()
    listener.config = config
    listener.start()
    assoc = connect(listener)
    assert not assoc.is_established


def test_disk_failure_never_returns_success(listener, image, monkeypatch):
    def fail(*_args):
        raise OSError("synthetic failure")

    monkeypatch.setattr(listener.storage, "_write_staged", fail)
    assoc = connect(listener)
    try:
        assert assoc.send_c_store(image).Status == 0xA700
    finally:
        assoc.release()
    with listener.storage.sessions() as session:
        assert session.scalar(select(func.count()).select_from(DicomInstance)) == 0


def test_malformed_pixels_are_rejected(listener, image):
    image.PixelData = b"\x00\x00"
    assoc = connect(listener)
    try:
        assert assoc.send_c_store(image).Status == 0xC210
    finally:
        assoc.release()
