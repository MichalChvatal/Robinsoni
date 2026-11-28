from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from mr_quality_monitor.config import ReceiverConfig
from mr_quality_monitor.database.models import DicomInstance
from mr_quality_monitor.dicom import sender
from mr_quality_monitor.dicom.listener import Listener
from mr_quality_monitor.dicom.sender import send
from mr_quality_monitor.dicom.storage import Storage
from pydicom import dcmwrite
from pydicom.uid import ImplicitVRLittleEndian, generate_uid
from sqlalchemy import func, select
from test_listener import listener  # noqa: F401


def submit(receiver, *paths: Path, calling_aet: str = "MR_TEST"):
    return send(
        paths,
        host="127.0.0.1",
        port=receiver.config.port,
        called_aet=receiver.config.ae_title,
        calling_aet=calling_aet,
    )


def test_sender_directory_zip_and_conflict(listener, image, tmp_path):  # noqa: F811
    folder = tmp_path / "files"
    folder.mkdir()
    first = folder / "dicom_without_suffix"
    dcmwrite(first, image, enforce_file_format=True)
    second = deepcopy(image)
    second.SOPInstanceUID = generate_uid()
    second.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    implicit = tmp_path / "implicit.dcm"
    dcmwrite(implicit, second, enforce_file_format=True)
    with ZipFile(folder / "images.zip", "w") as archive:
        archive.write(implicit, "nested/DICOM")
        archive.writestr("readme.txt", "Example")
    result = submit(listener, folder)
    assert (result.sent, result.skipped, result.failed) == (2, 1, 0)
    assert submit(listener, first).sent == 1
    with listener.storage.sessions() as session:
        assert session.scalar(select(func.count()).select_from(DicomInstance)) == 2

    image.PixelData = b"\x03\x00" * 16
    dcmwrite(first, image, enforce_file_format=True)
    result = submit(listener, first)
    assert (result.sent, result.failed, result.last_status) == (0, 1, "0xC211")
    with pytest.raises(ConnectionError):
        submit(listener, first, calling_aet="WRONG")


def test_repeat_send_distinguishes_discarded_study_from_conflict(tmp_path, image, monkeypatch):
    config = ReceiverConfig(storage_root=tmp_path / "store", single_active_study=True)
    receiver = Listener(config)
    receiver.storage = Storage(config.storage_root, 0, single_active_study=True)
    requestor = SimpleNamespace(address="127.0.0.1", ae_title="MR_TEST")

    def store(dataset):
        status = receiver._store(
            SimpleNamespace(
                assoc=SimpleNamespace(requestor=requestor),
                dataset=dataset,
                request=SimpleNamespace(
                    AffectedSOPInstanceUID=dataset.SOPInstanceUID,
                    AffectedSOPClassUID=dataset.SOPClassUID,
                ),
                context=SimpleNamespace(
                    abstract_syntax=dataset.SOPClassUID,
                    transfer_syntax=dataset.file_meta.TransferSyntaxUID,
                ),
            )
        )
        return SimpleNamespace(Status=status)

    association = SimpleNamespace(is_established=True, send_c_store=store, release=lambda: None)
    ae = SimpleNamespace(
        add_requested_context=lambda *_: None, associate=lambda *_, **__: association,
    )
    monkeypatch.setattr(sender, "AE", lambda **_: ae)

    first_path, second_path = tmp_path / "first.dcm", tmp_path / "second.dcm"
    second = deepcopy(image)
    second.StudyInstanceUID = generate_uid()
    second.SeriesInstanceUID = generate_uid()
    second.SOPInstanceUID = generate_uid()
    dcmwrite(first_path, image, enforce_file_format=True)
    dcmwrite(second_path, second, enforce_file_format=True)
    options = {"host": "127.0.0.1", "port": config.port,
               "called_aet": config.ae_title, "calling_aet": requestor.ae_title}
    try:
        assert send((first_path, second_path), **options).sent == 2
        repeated = send((first_path, second_path), report_discarded=True, **options)
        assert (repeated.sent, repeated.discarded, repeated.failed) == (1, 1, 0)
        assert not repeated.last_status
        strict = send((first_path,), **options)
        assert (strict.sent, strict.failed, strict.last_status) == (0, 1, "0xC212")

        second.PixelData = b"\x03\x00" * 16
        dcmwrite(second_path, second, enforce_file_format=True)
        conflict = send((second_path,), report_discarded=True, **options)
        assert (conflict.sent, conflict.discarded, conflict.failed) == (0, 0, 1)
        assert conflict.last_status == "0xC211"
        with receiver.storage.sessions() as session:
            assert list(session.scalars(select(DicomInstance.study_uid))) == [
                str(second.StudyInstanceUID)
            ]
    finally:
        receiver.storage.close()
