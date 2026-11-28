"""Send local DICOM files over an explicitly selected C-STORE association."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZipFile, is_zipfile

from pydicom import dcmread
from pydicom.errors import InvalidDicomError
from pynetdicom import AE

from .storage import DISCARDED_STUDY_STATUS, STORAGE_CLASSES, TRANSFER_SYNTAXES

if TYPE_CHECKING:
    from ..config import PacsConfig


@dataclass
class SendResult:
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    last_status: str = ""
    disconnected: bool = False
    discarded: int = 0


def inputs(paths: tuple[Path, ...]) -> Iterator:
    for path in paths:
        if path.is_dir():
            yield from inputs(tuple(sorted(item for item in path.rglob("*") if item.is_file())))
        elif path.suffix.lower() == ".zip" or is_zipfile(path):
            with ZipFile(path) as archive:
                for member in archive.infolist():
                    if not member.is_dir():
                        with archive.open(member) as stream:
                            yield stream
        else:
            yield path


def send(
    paths: tuple[Path, ...],
    *,
    host: str,
    port: int,
    called_aet: str,
    calling_aet: str,
    timeout: int = 30,
    report_discarded: bool = False,
) -> SendResult:
    """Send supported objects without transcoding and count listener acknowledgements."""
    ae = AE(ae_title=calling_aet)
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = timeout
    for sop_class in STORAGE_CLASSES:
        for syntax in TRANSFER_SYNTAXES:
            ae.add_requested_context(sop_class, syntax)
    association = ae.associate(host, port, ae_title=called_aet)
    if not association.is_established:
        raise ConnectionError("DICOM association was not established")
    result = SendResult()
    try:
        for source in inputs(paths):
            try:
                dataset = dcmread(source)
            except InvalidDicomError:
                result.skipped += 1
                continue
            except (OSError, ValueError, EOFError) as error:
                result.failed += 1
                result.last_status = type(error).__name__
                continue
            if (
                str(getattr(dataset, "SOPClassUID", "")) not in STORAGE_CLASSES
                or str(getattr(dataset.file_meta, "TransferSyntaxUID", "")) not in TRANSFER_SYNTAXES
            ):
                result.skipped += 1
                continue
            try:
                response = association.send_c_store(dataset)
            except (AttributeError, ValueError, TypeError) as error:
                result.failed += 1
                result.last_status = type(error).__name__
                continue
            status = getattr(response, "Status", None)
            if status == 0:
                result.sent += 1
            elif report_discarded and status == DISCARDED_STUDY_STATUS:
                result.discarded += 1
            else:
                result.failed += 1
                result.last_status = f"0x{status:04X}" if status is not None else "bez odpovědi"
            if not association.is_established:
                result.disconnected = True
                break
    finally:
        if association.is_established:
            association.release()
    return result


def send_dataset(path: Path, pacs: "PacsConfig") -> None:
    """Send a single stored DICOM file to the configured PACS SCP."""
    result = send(
        (path,),
        host=str(pacs.host),
        port=pacs.port,
        called_aet=pacs.called_aet,
        calling_aet=pacs.calling_aet,
    )
    if result.failed:
        raise ConnectionError(f"C-STORE failed: {result.last_status}")
