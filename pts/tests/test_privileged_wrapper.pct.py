# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_privileged_wrapper

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Privileged Wrapper Script Tests
#
# Runs the `appgarden-privileged` script as a subprocess to verify
# input validation and security restrictions.

# %%
#|export
import subprocess
import sys
from pathlib import Path

WRAPPER_SCRIPT = Path(__file__).resolve().parent.parent / "appgarden" / "templates" / "appgarden-privileged"

def _run_wrapper(*args: str) -> subprocess.CompletedProcess:
    """Run the wrapper script with given arguments, returning the result."""
    return subprocess.run(
        [sys.executable, str(WRAPPER_SCRIPT), *args],
        capture_output=True, text=True,
    )

# %% [markdown]
# ## No arguments

# %%
#|export
def test_no_args():
    """Wrapper with no arguments exits with error."""
    r = _run_wrapper()
    assert r.returncode != 0
    assert "Usage" in r.stderr

# %% [markdown]
# ## Unknown command

# %%
#|export
def test_unknown_command():
    """Wrapper rejects unknown commands."""
    r = _run_wrapper("badcmd")
    assert r.returncode != 0
    assert "Unknown command" in r.stderr

# %% [markdown]
# ## systemctl validation

# %%
#|export
def test_systemctl_no_action():
    """systemctl with no action is rejected."""
    r = _run_wrapper("systemctl")
    assert r.returncode != 0
    assert "missing action" in r.stderr

# %%
#|export
def test_systemctl_disallowed_action():
    """systemctl with disallowed action (e.g. mask) is rejected."""
    r = _run_wrapper("systemctl", "mask", "appgarden-foo.service")
    assert r.returncode != 0
    assert "disallowed action" in r.stderr

# %%
#|export
def test_systemctl_invalid_unit_name():
    """systemctl rejects unit names not matching appgarden-*.service."""
    r = _run_wrapper("systemctl", "restart", "nginx.service")
    assert r.returncode != 0
    assert "Invalid unit name" in r.stderr

# %%
#|export
def test_systemctl_path_traversal_unit():
    """systemctl rejects unit names with path traversal."""
    r = _run_wrapper("systemctl", "restart", "appgarden-../../etc/passwd.service")
    assert r.returncode != 0
    assert "Invalid unit name" in r.stderr or "Path traversal" in r.stderr

# %%
#|export
def test_systemctl_reload_only_caddy():
    """systemctl reload only allows caddy."""
    r = _run_wrapper("systemctl", "reload", "appgarden-foo.service")
    assert r.returncode != 0
    assert "only 'caddy' is allowed" in r.stderr

# %%
#|export
def test_systemctl_reload_caddy_accepted():
    """systemctl reload caddy is allowed (will fail due to no systemctl, but no validation error)."""
    r = _run_wrapper("systemctl", "reload", "caddy")
    # If systemctl is not available, we get a CalledProcessError exit, not a validation error
    # The key check: stderr should NOT contain our validation messages
    assert "only 'caddy' is allowed" not in r.stderr
    assert "disallowed action" not in r.stderr
    assert "Invalid unit name" not in r.stderr

# %%
#|export
def test_systemctl_daemon_reload_no_extra_args():
    """systemctl daemon-reload rejects extra arguments."""
    r = _run_wrapper("systemctl", "daemon-reload", "extra")
    assert r.returncode != 0
    assert "no extra arguments" in r.stderr

# %%
#|export
def test_systemctl_valid_unit_starts_with_number():
    """Unit name starting with non-alpha after prefix is rejected."""
    r = _run_wrapper("systemctl", "is-active", "appgarden-.bad.service")
    assert r.returncode != 0
    assert "Invalid unit name" in r.stderr

# %% [markdown]
# ## install-unit validation

# %%
#|export
def test_install_unit_wrong_args():
    """install-unit with wrong number of args is rejected."""
    r = _run_wrapper("install-unit", "only-one-arg")
    assert r.returncode != 0
    assert "expected" in r.stderr

# %%
#|export
def test_install_unit_bad_name():
    """install-unit rejects invalid unit names."""
    r = _run_wrapper("install-unit", "nginx.service", "/tmp/appgarden-unit-test.tmp")
    assert r.returncode != 0
    assert "Invalid unit name" in r.stderr

# %%
#|export
def test_install_unit_bad_temp_path():
    """install-unit rejects temp files not under /tmp/appgarden-unit-."""
    r = _run_wrapper("install-unit", "appgarden-foo.service", "/etc/passwd")
    assert r.returncode != 0
    assert "temp file must be under" in r.stderr

# %%
#|export
def test_install_unit_path_traversal_temp():
    """install-unit rejects path traversal in temp file."""
    r = _run_wrapper("install-unit", "appgarden-foo.service",
                     "/tmp/appgarden-unit-../../etc/shadow")
    assert r.returncode != 0
    assert "path traversal" in r.stderr.lower()

# %%
#|export
def test_install_unit_nonexistent_temp():
    """install-unit rejects non-existent temp file."""
    r = _run_wrapper("install-unit", "appgarden-foo.service",
                     "/tmp/appgarden-unit-nonexistent-12345.tmp")
    assert r.returncode != 0
    assert "does not exist" in r.stderr

# %% [markdown]
# ## remove-unit validation

# %%
#|export
def test_remove_unit_wrong_args():
    """remove-unit with wrong number of args is rejected."""
    r = _run_wrapper("remove-unit")
    assert r.returncode != 0
    assert "expected" in r.stderr

# %%
#|export
def test_remove_unit_bad_name():
    """remove-unit rejects invalid unit names."""
    r = _run_wrapper("remove-unit", "sshd.service")
    assert r.returncode != 0
    assert "Invalid unit name" in r.stderr

# %% [markdown]
# ## journalctl validation

# %%
#|export
def test_journalctl_no_unit():
    """journalctl with no unit is rejected."""
    r = _run_wrapper("journalctl")
    assert r.returncode != 0
    assert "missing unit" in r.stderr

# %%
#|export
def test_journalctl_bad_unit():
    """journalctl rejects non-appgarden unit names."""
    r = _run_wrapper("journalctl", "sshd.service")
    assert r.returncode != 0
    assert "Invalid unit name" in r.stderr

# %%
#|export
def test_journalctl_bad_lines_value():
    """journalctl rejects non-numeric --lines value."""
    r = _run_wrapper("journalctl", "appgarden-foo.service", "--lines", "abc")
    assert r.returncode != 0
    assert "invalid" in r.stderr.lower()

# %%
#|export
def test_journalctl_unexpected_args():
    """journalctl rejects unexpected extra arguments."""
    r = _run_wrapper("journalctl", "appgarden-foo.service", "--follow")
    assert r.returncode != 0
    assert "unexpected" in r.stderr.lower()
