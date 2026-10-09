"""Transactional test doubles; never selected by runtime composition."""

from copy import deepcopy
from threading import RLock
from app.application.planning import PlanService
from app.domain.planning import PlanUnavailableError
from tests.test_onboarding import fixture_service, NOW
from app.application.onboarding import OnboardingUpdate


class Store:
    def __init__(self):
        self.items, self.receipts, self.units = {}, {}, []
        self.lock = RLock()
        self.fail_commit = False


class Repository:
    def __init__(self, store):
        self.store = store

    def get(self, account_id, item_id, *, lock=False):
        item = self.store.items.get(item_id)
        return item if item and item.user_account_id == account_id else None

    def add(self, item):
        self.store.items[item.id] = item

    def update(self, item, expected_version):
        current = self.get(item.user_account_id, item.id)
        if not current or current.version != expected_version:
            return False
        self.store.items[item.id] = item
        return True

    def selected_date_facts(self, account_id, day):
        return [
            item
            for item in self.store.items.values()
            if item.user_account_id == account_id
            and item.intent.scheduled_for_date == day
        ]

    def today_facts(self, account_id, day, start, end):
        return [
            item
            for item in self.store.items.values()
            if item.user_account_id == account_id
            and (
                item.intent.scheduled_for_date == day
                or (item.completed_at is not None and start <= item.completed_at < end)
            )
        ]

    def receipt(self, account_id, operation, key):
        return self.store.receipts.get((account_id, operation, key))

    def save_receipt(self, account_id, operation, key, receipt):
        self.store.receipts[account_id, operation, key] = receipt


class Unit:
    def __init__(self, store):
        self.store = store
        self.plans = Repository(store)
        self.commits = 0
        store.units.append(self)

    def __enter__(self):
        self.store.lock.acquire()
        self.snapshot = deepcopy((self.store.items, self.store.receipts))
        return self

    def __exit__(self, *args):
        if not self.commits:
            self.rollback()
        self.store.lock.release()

    def commit(self):
        if self.store.fail_commit:
            raise PlanUnavailableError("Commit failed")
        self.commits += 1

    def rollback(self):
        self.store.items, self.store.receipts = self.snapshot


def fixture():
    account_id, account, states, _, onboarding = fixture_service()
    ready = onboarding.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["focus"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
                "goals": {"daily_steps": 8000},
            },
        ),
        "test-ready",
    )
    onboarding.complete(account_id, ready.version, "test-complete")
    store = Store()
    plans = PlanService(lambda _: Unit(store), clock=lambda: NOW)
    return account_id, account, states, onboarding, store, plans
