# blender_mcp_server.py
import json
import logging
import os
import platform
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from mcp.server.fastmcp import Context, FastMCP, Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BlenderMCPServer")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
MIN_PYTHON_VERSION = (3, 10)
MIN_BLENDER_VERSION = (3, 0, 0)
ISLAND_MODE = True


def _format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def validate_python_version() -> None:
    """Validate the Python runtime used by the MCP server."""
    if sys.version_info < MIN_PYTHON_VERSION:
        raise RuntimeError(
            f"BlenderMCP requires Python >= {_format_version(MIN_PYTHON_VERSION)}; "
            f"found {platform.python_version()}"
        )


def is_loopback_host(host: str) -> bool:
    """Return True only for explicit loopback hosts used by local Blender."""
    normalized = (host or "").strip().lower().strip("[]")
    return normalized in {"localhost", "127.0.0.1", "::1"}


def assert_loopback_host(host: str) -> None:
    """Reject non-loopback Blender hosts so commands cannot leave this machine."""
    if not is_loopback_host(host):
        raise ValueError(
            "Island mode only permits Blender connections to localhost, 127.0.0.1, or ::1. "
            f"Rejected host: {host!r}"
        )


def _disabled_remote_feature(feature_name: str) -> str:
    return (
        f"{feature_name} is disabled in island mode. "
        "This branch only allows Blender, the local runtime, and the active MCP/IDE/LLM runtime. "
        "Use locally stored assets or local Blender Python code instead."
    )


@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket | None = None

    def connect(self) -> bool:
        """Connect to the local Blender addon socket server."""
        if self.sock:
            return True

        assert_loopback_host(self.host)
        try:
            self.sock = socket.socket(socket.AF_INET6 if self.host == "::1" else socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info("Connected to Blender at %s:%s", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Failed to connect to Blender: %s", e)
            self.sock = None
            return False

    def disconnect(self) -> None:
        """Disconnect from the Blender addon."""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error("Error disconnecting from Blender: %s", e)
            finally:
                self.sock = None

    def receive_full_response(self, sock: socket.socket, buffer_size: int = 8192) -> bytes:
        """Receive a complete JSON response, potentially in multiple chunks."""
        chunks: list[bytes] = []
        sock.settimeout(180.0)

        while True:
            try:
                chunk = sock.recv(buffer_size)
                if not chunk:
                    if not chunks:
                        raise ConnectionError("Connection closed before receiving any data")
                    break
                chunks.append(chunk)
                data = b"".join(chunks)
                try:
                    json.loads(data.decode("utf-8"))
                    return data
                except json.JSONDecodeError:
                    continue
            except socket.timeout:
                break

        if not chunks:
            raise ConnectionError("No data received from Blender")

        data = b"".join(chunks)
        try:
            json.loads(data.decode("utf-8"))
            return data
        except json.JSONDecodeError as exc:
            raise ValueError("Incomplete JSON response received from Blender") from exc

    def send_command(self, command_type: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Send a command to local Blender and return the response."""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        command = {"type": command_type, "params": params or {}}
        try:
            assert self.sock is not None
            self.sock.sendall(json.dumps(command).encode("utf-8"))
            self.sock.settimeout(180.0)
            response_data = self.receive_full_response(self.sock)
            response = json.loads(response_data.decode("utf-8"))
            if response.get("status") == "error":
                raise RuntimeError(response.get("message", "Unknown error from Blender"))
            return response.get("result", {})
        except (ConnectionError, BrokenPipeError, ConnectionResetError, socket.timeout) as e:
            self.sock = None
            raise ConnectionError(f"Connection to Blender lost: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid response from Blender: {e}") from e
        except Exception:
            self.sock = None
            raise


_blender_connection: BlenderConnection | None = None


def get_blender_connection() -> BlenderConnection:
    """Get or create a persistent loopback-only Blender connection."""
    global _blender_connection
    if _blender_connection is not None:
        try:
            _blender_connection.send_command("get_environment_info")
            return _blender_connection
        except Exception as e:
            logger.warning("Existing Blender connection is no longer valid: %s", e)
            _blender_connection.disconnect()
            _blender_connection = None

    host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
    port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
    assert_loopback_host(host)
    _blender_connection = BlenderConnection(host=host, port=port)
    if not _blender_connection.connect():
        _blender_connection = None
        raise ConnectionError("Could not connect to Blender. Make sure the Blender addon is running locally.")
    return _blender_connection


def validate_blender_environment() -> None:
    """Validate the connected Blender addon version and island-mode policy."""
    blender = get_blender_connection()
    info = blender.send_command("get_environment_info")
    if not info.get("island_mode"):
        raise RuntimeError("Connected Blender addon is not running in island mode")

    blender_version = tuple(info.get("blender_version", []))
    if blender_version and blender_version < MIN_BLENDER_VERSION:
        raise RuntimeError(
            f"BlenderMCP requires Blender >= {_format_version(MIN_BLENDER_VERSION)}; "
            f"connected Blender is {_format_version(blender_version)}"
        )


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle without telemetry."""
    validate_python_version()
    try:
        logger.info("BlenderMCP island server starting up")
        try:
            validate_blender_environment()
            logger.info("Connected to compatible local Blender addon")
        except Exception as e:
            logger.warning("Could not validate Blender on startup: %s", e)
            logger.warning("Start the island-mode Blender addon before using Blender tools")
        yield {}
    finally:
        global _blender_connection
        if _blender_connection:
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP island server shut down")


mcp = FastMCP("BlenderMCP", lifespan=server_lifespan)


@mcp.tool()
def get_environment_info(ctx: Context) -> str:
    """Get Python, server, and connected Blender compatibility information."""
    info: dict[str, Any] = {
        "server_island_mode": ISLAND_MODE,
        "python_version": platform.python_version(),
        "minimum_python_version": _format_version(MIN_PYTHON_VERSION),
        "minimum_blender_version": _format_version(MIN_BLENDER_VERSION),
        "allowed_remote_endpoints": [],
    }
    try:
        blender = get_blender_connection()
        info["blender"] = blender.send_command("get_environment_info")
    except Exception as e:
        info["blender_error"] = str(e)
    return json.dumps(info, indent=2)


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene."""
    try:
        blender = get_blender_connection()
        return json.dumps(blender.send_command("get_scene_info"), indent=2)
    except Exception as e:
        logger.error("Error getting scene info from Blender: %s", e)
        return f"Error getting scene info: {e}"


@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """Get detailed information about a specific object in the Blender scene."""
    try:
        blender = get_blender_connection()
        return json.dumps(blender.send_command("get_object_info", {"name": object_name}), indent=2)
    except Exception as e:
        logger.error("Error getting object info from Blender: %s", e)
        return f"Error getting object info: {e}"


@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """Capture a screenshot of the current Blender 3D viewport without remote upload."""
    try:
        temp_path = str(Path(os.getenv("TMPDIR", "/tmp")) / f"blender_screenshot_{os.getpid()}.png")
        blender = get_blender_connection()
        result = blender.send_command(
            "get_viewport_screenshot",
            {"max_size": max_size, "filepath": temp_path, "format": "png"},
        )
        if "error" in result:
            raise RuntimeError(result["error"])
        path = Path(temp_path)
        if not path.exists():
            raise FileNotFoundError("Screenshot file was not created")
        image_bytes = path.read_bytes()
        path.unlink(missing_ok=True)
        return Image(data=image_bytes, format="png")
    except Exception as e:
        logger.error("Error capturing screenshot: %s", e)
        raise RuntimeError(f"Screenshot failed: {e}") from e


@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """Execute Python code in the local Blender runtime."""
    try:
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error("Error executing code: %s", e)
        return f"Error executing code: {e}"


@mcp.tool()
def get_polyhaven_status(ctx: Context) -> str:
    """Report that Poly Haven is unavailable in island mode."""
    return _disabled_remote_feature("Poly Haven")


@mcp.tool()
def get_sketchfab_status(ctx: Context) -> str:
    """Report that Sketchfab is unavailable in island mode."""
    return _disabled_remote_feature("Sketchfab")


@mcp.tool()
def get_hyper3d_status(ctx: Context) -> str:
    """Report that Hyper3D/Rodin is unavailable in island mode."""
    return _disabled_remote_feature("Hyper3D/Rodin")


@mcp.tool()
def get_hunyuan3d_status(ctx: Context) -> str:
    """Report that Hunyuan3D remote generation is unavailable in island mode."""
    return _disabled_remote_feature("Hunyuan3D")


@mcp.prompt()
def asset_creation_strategy() -> str:
    """Guide the model toward local-only asset creation."""
    return """
    Island-mode asset creation strategy:

    1. Do not use remote asset search, remote model libraries, telemetry, or cloud model generation.
       Poly Haven, Sketchfab, Hyper3D/Rodin, Fal, and Tencent Hunyuan official APIs are disabled.
    2. Prefer local Blender Python via execute_blender_code() for primitives, meshes, materials,
       lighting, cameras, animation, and procedural geometry.
    3. Use get_scene_info(), get_object_info(), and get_viewport_screenshot() to inspect local Blender state.
    4. If external assets are needed, ask the user to place already-approved files on the local filesystem,
       then import those local files with Blender Python. Do not request HTTP(S) URLs.
    5. If generated models are needed, use only tools that the user runs locally and imports as local files;
       do not send prompts, images, meshes, textures, or scene data to third-party services.
    """


def main() -> None:
    """Run the island-mode MCP server."""
    validate_python_version()
    mcp.run()


if __name__ == "__main__":
    main()
