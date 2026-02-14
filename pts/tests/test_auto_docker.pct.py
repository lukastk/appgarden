# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp test_auto_docker

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Auto-Docker Tests
#
# Unit tests for runtime detection, setup command inference,
# and Dockerfile generation.

# %%
#|export
from pathlib import Path

from appgarden.auto_docker import (
    detect_runtime, infer_setup_command, generate_dockerfile,
    Runtime, RUNTIMES,
)

# %% [markdown]
# ## detect_runtime

# %%
#|export
def test_detect_nodejs(tmp_path):
    """Detect Node.js from package.json."""
    (tmp_path / "package.json").write_text("{}")
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "nodejs"
    assert "node" in rt.base_image

# %%
#|export
def test_detect_python_pip(tmp_path):
    """Detect Python (pip) from requirements.txt."""
    (tmp_path / "requirements.txt").write_text("flask\n")
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "python-pip"
    assert "python" in rt.base_image

# %%
#|export
def test_detect_python_pyproject(tmp_path):
    """Detect Python from pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "python"

# %%
#|export
def test_detect_go(tmp_path):
    """Detect Go from go.mod."""
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "go"
    assert "golang" in rt.base_image

# %%
#|export
def test_detect_ruby(tmp_path):
    """Detect Ruby from Gemfile."""
    (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\n')
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "ruby"

# %%
#|export
def test_detect_rust(tmp_path):
    """Detect Rust from Cargo.toml."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'app'\n")
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "rust"

# %%
#|export
def test_detect_unknown(tmp_path):
    """Unknown project returns None."""
    (tmp_path / "main.c").write_text("int main() {}\n")
    rt = detect_runtime(tmp_path)
    assert rt is None

# %%
#|export
def test_detect_priority(tmp_path):
    """package.json takes priority over pyproject.toml."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    rt = detect_runtime(tmp_path)
    assert rt is not None
    assert rt.name == "nodejs"

# %% [markdown]
# ## infer_setup_command

# %%
#|export
def test_infer_setup_nodejs():
    """Node.js setup is npm install."""
    rt = Runtime(name="nodejs", base_image="node:22", setup_cmd="npm install")
    assert infer_setup_command(rt) == "npm install"

# %%
#|export
def test_infer_setup_python():
    """Python setup is pip install."""
    rt = Runtime(name="python-pip", base_image="python:3.12",
                 setup_cmd="pip install -r requirements.txt")
    assert "pip install" in infer_setup_command(rt)

# %% [markdown]
# ## generate_dockerfile

# %%
#|export
def test_generate_dockerfile_nodejs():
    """Generated Dockerfile for Node.js has correct structure."""
    rt = Runtime(name="nodejs", base_image="node:22",
                 setup_cmd="npm install", copy_first="package*.json")
    content = generate_dockerfile(rt, container_port=3000, cmd='["node", "server.js"]')
    assert "FROM node:22" in content
    assert "COPY package*.json ." in content
    assert "RUN npm install" in content
    assert "EXPOSE 3000" in content
    assert '["node", "server.js"]' in content

# %%
#|export
def test_generate_dockerfile_custom_setup():
    """Custom setup_cmd overrides the runtime default."""
    rt = Runtime(name="nodejs", base_image="node:22",
                 setup_cmd="npm install", copy_first="package*.json")
    content = generate_dockerfile(
        rt, container_port=8080, cmd="npm start",
        setup_cmd="npm ci --production",
    )
    assert "RUN npm ci --production" in content
    assert "npm install" not in content

# %%
#|export
def test_generate_dockerfile_python():
    """Generated Dockerfile for Python has correct structure."""
    rt = Runtime(name="python-pip", base_image="python:3.12",
                 setup_cmd="pip install -r requirements.txt",
                 copy_first="requirements.txt")
    content = generate_dockerfile(rt, container_port=5000, cmd="python app.py")
    assert "FROM python:3.12" in content
    assert "COPY requirements.txt ." in content
    assert "RUN pip install -r requirements.txt" in content
    assert "EXPOSE 5000" in content
