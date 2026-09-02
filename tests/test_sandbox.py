from pathlib import Path

from enigma.sandbox.local_sandbox import LocalSandbox


def test_run_script_reproduces_exception(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("raise ValueError('boom')\n")

    sandbox = LocalSandbox()
    result = sandbox.run_script(str(repo), "import service\n", timeout_seconds=5)

    assert result.reproduced is True
    assert "ValueError" in result.stderr


def test_run_script_no_error_means_not_reproduced(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("x = 1 + 1\n")

    sandbox = LocalSandbox()
    result = sandbox.run_script(str(repo), "import service\n", timeout_seconds=5)

    assert result.reproduced is False
    assert result.exit_code == 0


def test_run_script_respects_timeout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("while True: pass\n")

    sandbox = LocalSandbox()
    result = sandbox.run_script(str(repo), "import service\n", timeout_seconds=1)

    assert result.reproduced is False
    assert "timeout" in result.stderr.lower()


def test_run_tests_uses_same_interpreter_as_host(tmp_path):
    """Regression test for the env-stripping bug: sandbox must be able to
    find packages installed in the current interpreter/venv, not just the
    bare system python."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_x.py").write_text("def test_true():\n    assert True\n")

    sandbox = LocalSandbox()
    import sys
    result = sandbox.run_tests(str(repo), [sys.executable, "-m", "pytest", "-q"], timeout_seconds=15)

    assert result.passed is True
