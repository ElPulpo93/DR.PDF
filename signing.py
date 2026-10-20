"""Certificate-based digital signatures (PAdES), like Acrobat's "Sign with a digital ID".

A digital signature proves who signed and shows whether the document was changed after
signing. It uses a digital ID: a .pfx/.p12 file (from your organization or a provider),
or a self-signed ID created here."""
import datetime
import io
import os
import ssl
from pathlib import Path

import pymupdf as fitz

ID_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Dr.PDF" / "Digital IDs"


# the validation library logs a full error trace for every self-signed ID; the result is
# reported to the user in plain words instead
import logging  # noqa: E402
for _name in ("pyhanko", "pyhanko_certvalidator"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


# ------------------------------------------------------------------ digital IDs
def create_digital_id(name, email, organization, password, path=None, years=5):
    """Make a self-signed digital ID (.pfx) and return its path."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier

    if not name.strip():
        raise ValueError("Type your name.")
    if len(password) < 6:
        raise ValueError("Use a password of at least 6 characters.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, name.strip())]
    if organization.strip():
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization.strip()))
    if email.strip():
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email.strip()))
    subject = x509.Name(attrs)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365 * years))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False,
                                         decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.EMAIL_PROTECTION,
                ObjectIdentifier("1.3.6.1.4.1.311.10.3.12"),  # Microsoft: document signing
            ]), critical=False)
            .sign(key, hashes.SHA256()))
    data = pkcs12.serialize_key_and_certificates(
        name.strip().encode(), key, cert, None,
        serialization.BestAvailableEncryption(password.encode()))
    if path is None:
        ID_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in name if c.isalnum() or c in " ._-").strip() or "My ID"
        path = ID_DIR / f"{safe}.pfx"
        k = 2
        while path.exists():
            path = ID_DIR / f"{safe} ({k}).pfx"
            k += 1
    Path(path).write_bytes(data)
    return Path(path)


def known_ids():
    """Digital IDs created or used before (in the app's ID folder)."""
    if not ID_DIR.exists():
        return []
    return sorted(ID_DIR.glob("*.p*"), key=lambda p: p.stat().st_mtime, reverse=True)


def id_info(pfx_path, password):
    """(name, issuer, valid until) of a digital ID; raises ValueError for a wrong password."""
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(Path(pfx_path).read_bytes(),
                                                           password.encode())
    except ValueError:
        raise ValueError("That password doesn't open this digital ID.")
    if cert is None or key is None:
        raise ValueError("This file doesn't contain a usable digital ID.")
    return (_cn(cert.subject), _cn(cert.issuer), cert.not_valid_after_utc)


def _cn(name):
    from cryptography.x509.oid import NameOID
    v = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return v[0].value if v else name.rfc4514_string()


# ------------------------------------------------------------------ signing
def sign(pdf_bytes, out_path, pfx_path, password, page_index=None, rect=None, page_matrix=None,
         field_name=None, reason="", location="", doc_password=None):
    """Sign `pdf_bytes` and write the signed file to `out_path`.

    Either `field_name` (an existing empty signature field) or `page_index` + `rect`
    (where the visible signature goes, in the editor's page coordinates; `page_matrix`
    is that page's PyMuPDF transformation_matrix) must be given."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko.stamp import TextStampStyle

    signer = signers.SimpleSigner.load_pkcs12(str(pfx_path), passphrase=password.encode())
    if signer is None:
        raise ValueError("That password doesn't open this digital ID.")
    w = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes), strict=False)
    if doc_password:
        w.encrypt(doc_password)
    if field_name is None:
        existing = {f for f, _, _ in fields.enumerate_sig_fields(w)}
        field_name, k = "Signature1", 1
        while field_name in existing:
            k += 1
            field_name = f"Signature{k}"
        box = fitz.Rect(rect) * ~fitz.Matrix(page_matrix)
        fields.append_signature_field(w, fields.SigFieldSpec(
            sig_field_name=field_name, on_page=page_index,
            box=(box.x0, box.y0, box.x1, box.y1)))
    meta = signers.PdfSignatureMetadata(field_name=field_name, reason=reason or None,
                                        location=location or None)
    style = TextStampStyle(
        stamp_text="Digitally signed by %(signer)s\nDate: %(ts)s" +
                   (f"\nReason: {reason}" if reason else ""),
        timestamp_format="%m/%d/%Y %I:%M %p")
    pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=style)
    out = io.BytesIO()
    pdf_signer.sign_pdf(w, output=out)
    data = out.getvalue()
    tmp = Path(str(out_path) + ".drtmp")
    tmp.write_bytes(data)
    os.replace(tmp, out_path)
    return data


# ------------------------------------------------------------------ checking signatures
def _windows_roots():
    from asn1crypto import x509 as ax509
    roots = []
    for store in ("ROOT", "CA"):
        try:
            for der, enc, trust in ssl.enum_certificates(store):
                if enc == "x509_asn":
                    try:
                        roots.append(ax509.Certificate.load(der))
                    except Exception:
                        pass
        except Exception:
            pass
    return roots


def signatures(pdf_bytes, doc_password=None):
    """Check every signature. Returns a list of dicts with: field, signer, time, intact
    (document not changed since this signature), trusted (Windows trusts the signer's
    certificate), self_signed, summary (a sentence for people)."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    r = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    if r.encrypted and doc_password:
        r.decrypt(doc_password)
    sigs = r.embedded_signatures
    if not sigs:
        return []
    vc = ValidationContext(trust_roots=_windows_roots(), allow_fetching=False)
    out = []
    for s in sigs:
        info = {"field": s.field_name, "signer": "", "time": None, "intact": False,
                "trusted": False, "self_signed": False, "summary": ""}
        try:
            cert = s.signer_cert
            info["signer"] = cert.subject.native.get("common_name", "") or cert.subject.human_friendly
            info["self_signed"] = cert.self_signed in ("yes", "maybe")
            st = validate_pdf_signature(s, vc)
            info["time"] = st.signer_reported_dt
            info["intact"] = bool(st.intact and st.valid)
            info["trusted"] = bool(st.trusted)
            level = st.modification_level.name if st.modification_level is not None else "NONE"
            changed_after = st.coverage is not None and st.coverage.name != "ENTIRE_FILE"
            if not info["intact"]:
                info["summary"] = "Invalid — the document was changed after it was signed."
            elif not changed_after or level in ("NONE", "LTA_UPDATES"):
                info["summary"] = "Valid — the document hasn't changed since it was signed."
            elif level == "FORM_FILLING":
                info["summary"] = ("Valid — later changes were only other signatures or form "
                                   "entries, which are allowed.")
            elif level == "ANNOTATIONS":
                info["summary"] = "Valid — comments were added after it was signed."
            else:
                info["summary"] = ("The signature itself is valid, but the document was "
                                   "changed in other ways after it was signed.")
            if not info["trusted"]:
                info["summary"] += (" The signer's identity isn't verified: the digital ID is "
                                    "self-signed." if info["self_signed"] else
                                    " The signer's identity isn't verified by Windows.")
        except Exception as e:
            info["summary"] = f"Couldn't check this signature ({e.__class__.__name__}: {e})."
        out.append(info)
    return out


def has_signatures(doc):
    """Quick check with PyMuPDF: does the open document contain filled signature fields?"""
    for page in doc:
        for w in page.widgets(types=[fitz.PDF_WIDGET_TYPE_SIGNATURE]):
            if w.is_signed:
                return True
    return False


def count_signatures(doc):
    """Number of filled (signed) signature fields in an open PyMuPDF document."""
    n = 0
    for page in doc:
        for w in page.widgets(types=[fitz.PDF_WIDGET_TYPE_SIGNATURE]):
            if w.is_signed:
                n += 1
    return n
