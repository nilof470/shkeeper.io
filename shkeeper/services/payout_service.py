# app/services/payout_service.py
from decimal import Decimal
from urllib.parse import urlparse
from shkeeper import db
from shkeeper.models import Payout, PayoutTx
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.services.payout_errors import PayoutConflictError, PayoutRequestError
from sqlalchemy.exc import IntegrityError


class PayoutService:
    @staticmethod
    def get_crypto(crypto_name: str):
        try:
            return Crypto.instances[crypto_name]
        except KeyError:
            raise PayoutRequestError(
                f"Unknown crypto: {crypto_name}",
                code="UNKNOWN_CRYPTO",
                status_code=404,
            )

    @staticmethod
    def normalize_external_id(value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def check_external_id_unique(req, crypto_name):
        external_id = PayoutService.normalize_external_id(req.get("external_id"))
        if external_id:
            existing = Payout.query.filter_by(
                crypto=crypto_name,
                external_id=external_id,
            ).first()
            if existing:
                raise PayoutConflictError(
                    f"Payout with this external_id already exists: {external_id}"
                )
        return external_id

    @staticmethod
    def validate_callback_url(callback_url):
        if not callback_url:
            return
        parsed = urlparse(callback_url)
        if not parsed.scheme or not parsed.netloc:
            raise PayoutRequestError(
                f"Invalid callback_url: {callback_url}",
                code="INVALID_CALLBACK_URL",
            )
        if parsed.scheme not in ("http", "https"):
            raise PayoutRequestError(
                f"Invalid callback_url scheme: {callback_url}",
                code="INVALID_CALLBACK_URL",
            )

    @staticmethod
    def get_destination(req):
        destination = req.get("destination") or req.get("dest")
        if not destination:
            raise PayoutRequestError(
                "destination is required",
                code="INVALID_DESTINATION",
            )
        return destination

    @staticmethod
    def parse_amount(req):
        try:
            return Decimal(req["amount"])
        except Exception as exc:
            raise PayoutRequestError(
                "Payout amount should be a valid decimal number",
                code="INVALID_AMOUNT",
            ) from exc

    @staticmethod
    def validate_positive_amount(amount):
        if amount <= 0:
            raise PayoutRequestError(
                "Payout amount should be a positive number",
                code="INVALID_AMOUNT",
            )

    @staticmethod
    def get_request_fee(crypto, req):
        if "fee" in req:
            return req["fee"]
        can_omit = getattr(crypto, "can_omit_fee_for_payout", False)
        if callable(can_omit):
            can_omit = can_omit()
        if can_omit:
            return "0"
        raise PayoutRequestError("fee is required", code="FEE_REQUIRED")

    @staticmethod
    def preflight_payout(crypto, req):
        preflight = getattr(crypto, "preflight_payout", None)
        if callable(preflight):
            try:
                preflight(
                    destination=PayoutService.get_destination(req),
                    amount=Decimal(req["amount"]),
                )
            except PayoutRequestError:
                raise
            except ValueError as exc:
                raise PayoutRequestError(str(exc)) from exc

    @staticmethod
    def mark_payout_failed(payout, message):
        from shkeeper.models import PayoutStatus

        payout.status = PayoutStatus.FAIL
        payout.success = "No"
        payout.error = str(message)
        db.session.commit()

    @staticmethod
    def mark_payout_enqueue_unknown(payout, message):
        payout.error = f"Sidecar enqueue result is unknown: {message}"
        db.session.commit()

    @staticmethod
    def mark_payout_enqueue_pending(payout):
        payout.error = "Payout enqueue pending"
        db.session.commit()

    @staticmethod
    def clear_payout_error(payout):
        payout.error = None
        db.session.commit()

    @staticmethod
    def extract_task_id(res):
        if isinstance(res, dict):
            return res.get("task_id")
        return None

    @staticmethod
    def extract_direct_txids(res):
        if not isinstance(res, dict):
            return []
        if res.get("txids"):
            txids = res["txids"]
        elif res.get("txid"):
            txids = res["txid"]
        elif res.get("result"):
            txids = res["result"]
        else:
            return []
        if isinstance(txids, str):
            return [txids]
        return list(txids)

    @staticmethod
    def is_direct_payout_response(res):
        return isinstance(res, dict) and any(
            key in res for key in ("result", "txid", "txids", "error")
        )

    @staticmethod
    def add_payout_txids(payout, txids):
        for txid in PayoutService.extract_direct_txids({"txids": txids}):
            if not any(t.txid == txid for t in payout.transactions):
                db.session.add(PayoutTx(payout_id=payout.id, txid=txid))
        db.session.commit()

    @staticmethod
    def create_payout_record(req, crypto_name, task_id=None, txids=None):
        callback_url = req.get("callback_url")
        PayoutService.validate_callback_url(callback_url)
        destination = PayoutService.get_destination(req)
        return Payout.add(
            {
                "dest": destination,
                "amount": Decimal(req["amount"]),
                "callback_url": callback_url,
                "txids": txids or [],
            },
            crypto_name,
            task_id=task_id,
            external_id=PayoutService.normalize_external_id(req.get("external_id")),
        )

    @classmethod
    def single_payout(cls, crypto_name, req):
        crypto = cls.get_crypto(crypto_name)
        external_id = cls.check_external_id_unique(req, crypto_name)
        cls.validate_callback_url(req.get("callback_url"))
        amount = cls.parse_amount(req)
        cls.validate_positive_amount(amount)
        cls.preflight_payout(crypto, req)
        fee = cls.get_request_fee(crypto, req)

        if external_id:
            return cls._single_payout_with_reserved_external_id(
                crypto_name,
                crypto,
                req,
                fee,
                external_id,
            )

        res = crypto.mkpayout(
            cls.get_destination(req),
            amount,
            fee,
        )
        task_id = cls.extract_task_id(res)
        if not task_id and not cls.is_direct_payout_response(res):
            raise PayoutRequestError(f"Payout sidecar did not return task_id: {res}")
        cls.create_payout_record(
            req,
            crypto_name,
            task_id=task_id,
            txids=cls.extract_direct_txids(res),
        )
        return res

    @classmethod
    def _single_payout_with_reserved_external_id(
        cls,
        crypto_name,
        crypto,
        req,
        fee,
        external_id,
    ):
        req = dict(req)
        req["external_id"] = external_id
        try:
            payout = cls.create_payout_record(req, crypto_name, task_id=None)
        except IntegrityError as exc:
            db.session.rollback()
            raise PayoutConflictError(
                f"Payout with this external_id already exists: {external_id}"
            ) from exc
        cls.mark_payout_enqueue_pending(payout)

        try:
            res = crypto.mkpayout(
                cls.get_destination(req),
                Decimal(req["amount"]),
                fee,
            )
        except Exception as exc:
            cls.mark_payout_enqueue_unknown(payout, exc)
            raise

        task_id = cls.extract_task_id(res)
        if task_id:
            payout.task_id = task_id
            payout.error = None
            db.session.commit()
            res["external_id"] = external_id
            return res

        if cls.is_direct_payout_response(res):
            txids = cls.extract_direct_txids(res)
            cls.add_payout_txids(payout, txids)
            if res.get("error") and not txids:
                cls.mark_payout_failed(payout, res["error"])
            else:
                cls.clear_payout_error(payout)
            res["external_id"] = external_id
            return res

        if not task_id:
            cls.mark_payout_failed(payout, res)
            raise PayoutRequestError(f"Payout sidecar did not return task_id: {res}")

    @classmethod
    def validate_multipayout_before_enqueue(cls, crypto_name, payout_list):
        for req in payout_list:
            cls.validate_callback_url(req.get("callback_url"))
            external_id = cls.check_external_id_unique(req, crypto_name)
            if not external_id:
                continue
            raise PayoutRequestError(
                "external_id is not supported for multipayout in this phase",
                code="MULTIPAYOUT_EXTERNAL_ID_UNSUPPORTED",
                status_code=400,
            )
        return []

    @classmethod
    def multiple_payout(cls, crypto_name, payout_list):
        if not isinstance(payout_list, list):
            raise PayoutRequestError(
                "Expected an array of payouts",
                code="INVALID_PAYOUT_LIST",
            )

        crypto = cls.get_crypto(crypto_name)
        normalized_external_ids = cls.validate_multipayout_before_enqueue(
            crypto_name,
            payout_list,
        )
        res = crypto.multipayout(payout_list)
        task_id = cls.extract_task_id(res)
        if not task_id:
            raise PayoutRequestError(
                f"Multipayout sidecar did not return task_id: {res}"
            )

        created_ids = []
        for req in payout_list:
            payout = cls.create_payout_record(req, crypto_name, task_id=task_id)
            created_ids.append(payout.id)
        res["external_ids"] = normalized_external_ids
        return res
