"""Provider-neutral authentication email delivery contracts and orchestration."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.application.authentication_persistence import AuthenticationUnitOfWork
from app.domain.authentication import AuthenticationEmailOutboxRecord


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    recipient: str = field(repr=False)
    sender: str
    subject: str
    text: str = field(repr=False)
    html: str = field(repr=False)
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    provider_message_id: str = field(repr=False)


class EmailDeliveryError(RuntimeError):
    def __init__(self, category: str, *, retryable: bool) -> None:
        super().__init__("Authentication email delivery failed")
        self.category = category
        self.retryable = retryable


class RedactedEmailDeliveryCause(RuntimeError):
    """Safe traceback node for provider internals."""


def raise_safe_delivery_error(
    category: str, *, retryable: bool, internal_cause: BaseException
) -> None:
    error = EmailDeliveryError(category, retryable=retryable)
    error._internal_cause = internal_cause  # type: ignore[attr-defined]
    raise error from RedactedEmailDeliveryCause(
        f"{type(internal_cause).__name__} details redacted"
    )


class EmailDeliveryProvider(Protocol):
    def send(self, message: OutboundEmail) -> DeliveryReceipt: ...


class AuthenticationEmailRenderer(Protocol):
    def render(self, record: AuthenticationEmailOutboxRecord) -> OutboundEmail: ...


@dataclass(frozen=True, slots=True)
class DispatchSummary:
    claimed: int
    delivered: int
    retry_scheduled: int
    failed: int


class AuthenticationEmailDispatcher:
    """Claims a bounded batch, then records provider outcomes transactionally."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[str], AuthenticationUnitOfWork],
        renderer: AuthenticationEmailRenderer,
        provider: EmailDeliveryProvider,
        *,
        clock: Callable[[], datetime] | None = None,
        retry_delay: timedelta = timedelta(minutes=1),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._renderer = renderer
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_delay = retry_delay

    def process_pending(self, limit: int) -> DispatchSummary:
        if limit < 1 or limit > 100:
            raise ValueError("Delivery batch limit must be between 1 and 100")
        now = self._clock()
        claim_id = uuid4()
        with self._unit_of_work_factory("email-delivery-claim") as unit:
            records = list(unit.email_outbox.claim_available(now, claim_id, limit))
            unit.commit()
        delivered = retried = failed = 0
        for record in records:
            try:
                message = self._renderer.render(record)
                receipt = self._provider.send(message)
            except EmailDeliveryError as error:
                with self._unit_of_work_factory("email-delivery-result") as unit:
                    if error.retryable and record.attempt_count < 4:
                        unit.email_outbox.release_for_retry(
                            record.id, claim_id, now + self._retry_delay, error.category
                        )
                        retried += 1
                    else:
                        unit.email_outbox.mark_failed(
                            record.id, claim_id, now, error.category
                        )
                        failed += 1
                    unit.commit()
            else:
                with self._unit_of_work_factory("email-delivery-result") as unit:
                    unit.email_outbox.mark_delivered(
                        record.id, claim_id, now, receipt.provider_message_id
                    )
                    unit.commit()
                delivered += 1
        return DispatchSummary(len(records), delivered, retried, failed)
