from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application import (
    MockResourceService,
    ResourceRepository,
    ResourceUnitOfWork,
)
from app.domain import ItemNotFoundError
from app.infrastructure import (
    InMemoryResourceStore,
    InMemoryResourceUnitOfWork,
    InMemoryResourceUnitOfWorkFactory,
)
from app.main import create_app


class RecordingInMemoryUnitOfWork(InMemoryResourceUnitOfWork):
    def __init__(self, repository: InMemoryResourceStore) -> None:
        super().__init__(repository)
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_count += 1
        super().rollback()


class RecordingUnitOfWorkFactory:
    def __init__(self, repository: InMemoryResourceStore) -> None:
        self.repository = repository
        self.created: list[RecordingInMemoryUnitOfWork] = []

    def __call__(self) -> RecordingInMemoryUnitOfWork:
        unit_of_work = RecordingInMemoryUnitOfWork(self.repository)
        self.created.append(unit_of_work)
        return unit_of_work


def test_in_memory_adapter_satisfies_application_persistence_contracts():
    repository = InMemoryResourceStore()
    unit_of_work = InMemoryResourceUnitOfWork(repository)

    assert isinstance(repository, ResourceRepository)
    assert isinstance(unit_of_work, ResourceUnitOfWork)
    assert not hasattr(repository, "commit")
    assert not hasattr(repository, "rollback")


def test_in_memory_unit_of_work_restores_uncommitted_partial_changes():
    repository = InMemoryResourceStore()
    original_tasks = repository.read("tasks")

    with InMemoryResourceUnitOfWork(repository) as unit_of_work:
        unit_of_work.resources.write(
            "tasks",
            [{"id": "uncommitted", "title": "Must be rolled back"}],
        )

    assert repository.read("tasks") == original_tasks


def test_application_service_commits_one_successful_mutation_once():
    factory = RecordingUnitOfWorkFactory(InMemoryResourceStore())
    service = MockResourceService(factory)

    item = service.add_item("tasks", {"id": "uow", "title": "One commit"})

    assert item == {"id": "uow", "title": "One commit"}
    assert len(factory.created) == 1
    assert factory.created[0].commit_count == 1
    assert factory.created[0].rollback_count == 0


def test_application_service_rolls_back_a_failed_operation_and_does_not_commit():
    repository = InMemoryResourceStore()
    factory = RecordingUnitOfWorkFactory(repository)
    service = MockResourceService(factory)
    original_tasks: list[dict[str, Any]] = repository.read("tasks")

    with pytest.raises(ItemNotFoundError):
        service.update_item("tasks", "missing", {"done": True})

    assert repository.read("tasks") == original_tasks
    assert len(factory.created) == 1
    assert factory.created[0].commit_count == 0
    assert factory.created[0].rollback_count == 1


def test_each_application_operation_receives_a_new_unit_of_work():
    factory = RecordingUnitOfWorkFactory(InMemoryResourceStore())
    service = MockResourceService(factory)

    service.get_resource("dashboard")
    service.get_resource("tasks")

    assert len(factory.created) == 2
    assert factory.created[0] is not factory.created[1]


def test_explicitly_injected_unit_of_work_factories_keep_app_state_isolated():
    first_store = InMemoryResourceStore()
    second_store = InMemoryResourceStore()
    first_client = TestClient(
        create_app(
            resource_unit_of_work_factory=InMemoryResourceUnitOfWorkFactory(
                first_store
            )
        )
    )
    second_client = TestClient(
        create_app(
            resource_unit_of_work_factory=InMemoryResourceUnitOfWorkFactory(
                second_store
            )
        )
    )
    item = {"id": "injected", "title": "Only in one injected store"}

    first_client.post("/tasks", json=item)

    first_item = first_client.get("/tasks").json()[0]
    assert first_item["id"] == item["id"]
    assert first_item["title"] == item["title"]
    assert all(
        task["id"] != "injected"
        for task in second_client.get("/tasks").json()
    )


def test_demo_reset_remains_transactional_and_restores_seed_data():
    repository = InMemoryResourceStore()
    service = MockResourceService(InMemoryResourceUnitOfWorkFactory(repository))
    item = {"id": "reset-me", "title": "Temporary"}

    service.add_item("tasks", item)
    assert service.get_resource("tasks")[0] == item

    service.reset_demo()

    assert all(task["id"] != "reset-me" for task in service.get_resource("tasks"))
