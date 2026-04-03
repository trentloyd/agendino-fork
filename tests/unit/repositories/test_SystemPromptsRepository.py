import os
import tempfile

import pytest

from repositories.SystemPromptsRepository import SystemPromptsRepository


class TestSystemPromptsRepository:
    @pytest.fixture
    def prompts_dir(self, tmp_path):
        """Create a temporary system_prompts directory structure."""
        base = tmp_path / "system_prompts"
        # it/Generale/SintesiAdattiva.txt
        (base / "it" / "Generale").mkdir(parents=True)
        (base / "it" / "Generale" / "SintesiAdattiva.txt").write_text("Prompt A content", encoding="utf-8")
        # it/Riunione/MinuteMeeting.txt
        (base / "it" / "Riunione").mkdir(parents=True)
        (base / "it" / "Riunione" / "MinuteMeeting.txt").write_text("Prompt B content", encoding="utf-8")
        # en/General/Summary.txt
        (base / "en" / "General").mkdir(parents=True)
        (base / "en" / "General" / "Summary.txt").write_text("Prompt C content", encoding="utf-8")
        return str(base)

    @pytest.fixture
    def repo(self, prompts_dir):
        return SystemPromptsRepository(prompts_dir)

    def test_get_all_returns_all_prompts(self, repo):
        prompts = repo.get_all()
        assert len(prompts) == 3

        ids = [p["id"] for p in prompts]
        assert "it/Generale/SintesiAdattiva" in ids
        assert "it/Riunione/MinuteMeeting" in ids
        assert "en/General/Summary" in ids

    def test_get_all_prompt_structure(self, repo):
        prompts = repo.get_all()
        for p in prompts:
            assert "id" in p
            assert "label" in p
            assert "language" in p
            assert "category" in p

    def test_get_all_labels(self, repo):
        prompts = repo.get_all()
        labels = {p["id"]: p["label"] for p in prompts}
        assert labels["it/Generale/SintesiAdattiva"] == "Generale / SintesiAdattiva"
        assert labels["en/General/Summary"] == "General / Summary"

    def test_get_all_languages(self, repo):
        prompts = repo.get_all()
        languages = {p["id"]: p["language"] for p in prompts}
        assert languages["it/Generale/SintesiAdattiva"] == "it"
        assert languages["en/General/Summary"] == "en"

    def test_get_all_empty_directory(self, tmp_path):
        repo = SystemPromptsRepository(str(tmp_path / "empty"))
        assert repo.get_all() == []

    def test_get_all_nonexistent_directory(self, tmp_path):
        repo = SystemPromptsRepository(str(tmp_path / "does_not_exist"))
        assert repo.get_all() == []

    def test_get_prompt_content(self, repo):
        content = repo.get_prompt_content("it/Generale/SintesiAdattiva")
        assert content == "Prompt A content"

    def test_get_prompt_content_not_found(self, repo):
        content = repo.get_prompt_content("it/Generale/NonExistent")
        assert content is None

    def test_ignores_non_txt_files(self, tmp_path):
        base = tmp_path / "prompts"
        (base / "it" / "Generale").mkdir(parents=True)
        (base / "it" / "Generale" / "good.txt").write_text("ok")
        (base / "it" / "Generale" / "bad.md").write_text("skip me")
        (base / "it" / "Generale" / "bad.json").write_text("{}")

        repo = SystemPromptsRepository(str(base))
        prompts = repo.get_all()
        assert len(prompts) == 1
        assert prompts[0]["id"] == "it/Generale/good"

    def test_ignores_files_at_lang_level(self, tmp_path):
        base = tmp_path / "prompts"
        (base / "it").mkdir(parents=True)
        (base / "it" / "stray_file.txt").write_text("should be ignored")
        (base / "it" / "Category").mkdir()
        (base / "it" / "Category" / "Prompt.txt").write_text("valid")

        repo = SystemPromptsRepository(str(base))
        prompts = repo.get_all()
        assert len(prompts) == 1
