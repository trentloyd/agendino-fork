import os

import pytest

from repositories.LocalRecordingsRepository import LocalRecordingsRepository


class TestLocalRecordingsRepository:
    @pytest.fixture
    def local_recordings_path(self):
        path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(path, "../../../local_recordings")

    @pytest.fixture
    def local_recordings_repository(self, local_recordings_path) -> LocalRecordingsRepository:
        return LocalRecordingsRepository(local_recordings_path)

    def test_it_can_get_all(self, local_recordings_repository):
        result = local_recordings_repository.get_all()
        print(result)
