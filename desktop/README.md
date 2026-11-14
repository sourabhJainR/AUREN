# AUREN Desktop

AUREN Desktop is a native Tauri 2 shell around the existing single-user local experience. It does not create a second runtime, controller, MCP server, task store or Guard.

## What it does

- Starts the installed AUREN CLI with `auren up`.
- Lets AUREN choose free loopback ports for the dashboard and MCP service.
- Waits for the CLI to report both endpoints ready.
- Embeds the existing Engineering Console in the native window.
- Displays the MCP endpoint for local MCP clients.
- Provides a native folder picker for project selection.
- Detects when the managed CLI process exits unexpectedly.
- Stops the managed CLI process when the user stops AUREN or closes the desktop window.

The shell starts AUREN with automatic client configuration and browser launching disabled. It does not edit Codex, Claude, Gemini or other client configuration files.

## Prerequisites

- Either the `auren` launcher on PATH, or an AUREN checkout with `auren_cli.py`.
- For a checkout, set `AUREN_CLI_PATH` to the full `auren_cli.py` path. Optionally set `AUREN_PYTHON_EXECUTABLE`; it defaults to `python.exe` on Windows.
- Node.js and npm.
- Rust stable toolchain.
- Windows: Microsoft C++ Build Tools and WebView2 runtime.

To use a non-default CLI executable, set `AUREN_EXECUTABLE` to its full path. This takes priority over `AUREN_CLI_PATH`.

## Development

From this directory:

```powershell
npm install
npm run dev
```

Build Windows installers:

```powershell
npm run build
```

The Tauri build creates NSIS and MSI packages under `src-tauri/target/release/bundle`.

## Security and process ownership

- The shell accepts only the loopback endpoint lines emitted by the AUREN CLI.
- The local dashboard and MCP host remain bound to IPv4 loopback by AUREN.
- No bearer token is passed to the webview or stored by the shell.
- The MCP token remains in the existing owner-only AUREN credential file.
- The shell launches the existing CLI with argument-safe process APIs; it does not invoke a command shell.
- One AUREN runtime is managed per desktop process. Switching projects requires stopping the current runtime first.
- Closing the desktop window terminates the managed CLI process.

## Release boundary

This first native shell requires the AUREN CLI to be installed separately. It is a native desktop application, but the CLI is not yet bundled as a signed sidecar in the installer. A self-contained installer, code signing, update channel, crash reporting and full Windows/macOS/Linux release acceptance remain separate release gates.
