"""Read-only Today orchestration; boundaries are derived from server time."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from app.application.planning import PlanService
from app.domain.onboarding import OnboardingState
from app.domain.planning import timezone_info, utc
from app.domain.today import Today, aggregate


class TodayService:
    def __init__(self, plans: PlanService) -> None:
        self.plans = plans

    def get(
        self,
        account_id: UUID,
        timezone: str,
        onboarding: OnboardingState,
        request_id: str,
    ) -> Today:
        zone = timezone_info(timezone)
        now = utc(self.plans.clock())
        day = now.astimezone(zone).date()
        start = datetime.combine(day, time.min, zone).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
        with self.plans.factory(request_id) as unit:
            items = unit.plans.today_facts(account_id, day, start, end)
            completions = items
        result = aggregate(
            account_id,
            now,
            timezone,
            items,
            completions,
            onboarding.priority_life_areas,
            onboarding.goals,
        )
        if self.plans.diagnostic:
            self.plans.diagnostic.emit("today_read", request_id, account_id, None, 0)
        return result
