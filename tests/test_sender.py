from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
from mr_quality_monitor.database.models import DicomInstance
from mr_quality_monitor.dicom.sender import send
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
