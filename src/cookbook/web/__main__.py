"""python -m cookbook.web [--port 8000]

Starts the page on this computer only (127.0.0.1). The Gemini key is read from .env here, on the
server, and never sent to the browser.
"""

import argparse
import webbrowser


def main() -> int:
    ap = argparse.ArgumentParser(description="Start the Cookbook Challenge page on this computer.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="do not open the page in the browser")
    args = ap.parse_args()

    try:
        import uvicorn

        from .app import create_app
    except ImportError:
        print('error: the web page needs extra packages: pip install -e ".[web]"')
        return 1
    from cookbook.llm import load_env

    load_env()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Cookbook Challenge: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
