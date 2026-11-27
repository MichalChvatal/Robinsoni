import argparse
import logging
from pathlib import Path
from uuid import uuid4

from pynetdicom import AE, AllStoragePresentationContexts, evt
from pynetdicom.sop_class import Verification

OUTPUT_DIR = Path(__file__).resolve().parent / "received"


def handle_store(event):
    destination = OUTPUT_DIR / f"{uuid4().hex}.dcm"
    temporary = destination.with_suffix(".tmp")

    try:
        encoded_dataset = event.encoded_dataset()
    except Exception:
        logging.exception("Could not encode received DICOM instance")
        return 0xC000

    try:
        temporary.write_bytes(encoded_dataset)
        temporary.replace(destination)
    except OSError:
        logging.exception("Could not store DICOM instance")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logging.exception("Could not remove temporary DICOM file")
        return 0xA700

    logging.info("Stored %s", destination.name)
    return 0x0000


def main():
    parser = argparse.ArgumentParser(description="DICOM Storage SCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11112)
    parser.add_argument("--ae-title", default="DICOM_LISTENER")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    OUTPUT_DIR.mkdir(exist_ok=True)

    ae = AE(ae_title=args.ae_title)
    ae.supported_contexts = AllStoragePresentationContexts
    ae.add_supported_context(Verification)
    ae.require_called_aet = True

    logging.info("Starting %s on %s:%d", args.ae_title, args.host, args.port)
    ae.start_server(
        (args.host, args.port),
        evt_handlers=[(evt.EVT_C_STORE, handle_store)],
    )


if __name__ == "__main__":
    main()
