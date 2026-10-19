"""Browser test of the passkey flow: the real dashboard in Google Chrome with a virtual WebAuthn
authenticator (Chrome DevTools Protocol), against a private chain and its own API and web server.

    make e2e-passkey

It registers a passkey, approves an over-limit payment with it, locks the owner key, freezes and unfreezes
with the passkey, and checks that cancelling signs nothing. The vault verifies every signature on-chain.
Needs Google Chrome (or run `uv run --with playwright playwright install chromium` once). No model needed.
Screenshots go to scripts/e2e-shots/ (git-ignored).
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from hufu.chain import Chain
from hufu.devchain import devchain
from hufu.guard import Guard
from hufu.store import Store
from hufu.tools import TOOLS_BY_NAME, Context

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "scripts" / "e2e-shots"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError(f"{url} did not come up")


def file_over_limit_payment(ctx: Context, amount: int) -> int:
    """What the agent does when asked for more than its per-payment limit: file a request (no model needed)."""
    guard = Guard(ctx, anchor=False)
    guard.begin_turn(f"pay the landlord {amount} HKD for the flat deposit")
    r = guard.execute(
        ctx, TOOLS_BY_NAME["pay"], {"payee": "landlord", "token": "tHKD", "amount": amount, "memo": "deposit"}
    )
    return int(r["approval_id"])


def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    api_port, web_port = free_port(), free_port()
    db = Path(tempfile.mkdtemp()) / "e2e.db"
    with devchain() as settings:
        env = os.environ | {
            "RPC_URL": settings.rpc_url,
            "CHAIN_ID": str(settings.chain_id),
            "DB_PATH": str(db),
            "WEB_ORIGIN": f"http://localhost:{web_port}",
            "AUTOPILOT": "false",
        }
        api = subprocess.Popen(
            ["uv", "run", "uvicorn", "hufu.api:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=REPO / "agent",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        web = subprocess.Popen(
            ["pnpm", "exec", "next", "dev", "-p", str(web_port)],
            cwd=REPO / "web",
            env=os.environ | {"NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{api_port}"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for(f"http://127.0.0.1:{api_port}/passkey")
            wait_for(f"http://localhost:{web_port}/")
            chain = Chain(settings)
            ctx = Context(chain, Store(db, chain.vault.address))
            ctx.store.bind_deployment(chain.deployment_marker())
            run_browser(f"http://localhost:{web_port}", ctx)
        finally:
            web.terminate()
            api.terminate()


def run_browser(url: str, ctx: Context) -> None:
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except PlaywrightError:
            browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1440, "height": 1100}).new_page()
        cdp = page.context.new_cdp_session(page)
        cdp.send("WebAuthn.enable", {"enableUI": False})
        cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,  # the virtual fingerprint always matches
                    "automaticPresenceSimulation": True,
                }
            },
        )
        page.goto(url)
        card = page.locator("[data-slot=card]", has_text="Your half, on your device")
        expect(card).to_be_visible(timeout=120_000)

        def sign() -> None:
            page.get_by_role("dialog").get_by_role("button", name="Sign with passkey").click()

        page.get_by_role("button", name="Set up passkey").click()
        expect(page.get_by_text("Passkey registered on the vault")).to_be_visible(timeout=30_000)
        step("registered a passkey (the owner key stored its public key on the vault)")

        file_over_limit_payment(ctx, 6000)
        page.get_by_role("button", name="Approve with passkey").click()
        expect(page.get_by_role("dialog")).to_contain_text("Approve: Pay 6000 tHKD to landlord")
        page.screenshot(path=SHOTS / "sign-dialog.png")
        sign()
        expect(page.get_by_text("approved · success")).to_be_visible(timeout=30_000)
        step("approved a 6,000 HKD payment with the passkey (verified on-chain)")

        page.get_by_role("button", name="Lock owner key").click()
        expect(page.get_by_role("dialog")).to_contain_text("Lock the owner key")
        sign()
        expect(page.get_by_text("Owner key locked: it has no power left")).to_be_visible(timeout=30_000)
        card.screenshot(path=SHOTS / "owner-key-locked.png")
        approval_id = file_over_limit_payment(ctx, 5500)
        stolen = ctx.chain.approve_request(ctx.store.approval(approval_id)["onchain_id"])
        assert stolen.error == "NotOwner()", stolen
        step("locked the owner key; the vault now refuses it (NotOwner)")

        page.get_by_role("button", name="Freeze", exact=True).click()
        page.get_by_role("button", name="Unfreeze (passkey)").click()
        sign()
        expect(page.get_by_role("button", name="Freeze", exact=True)).to_be_visible(timeout=30_000)
        step("froze without approval, unfroze with the passkey")

        page.get_by_role("button", name="Approve with passkey").click()
        page.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        expect(page.get_by_text("Cancelled. Nothing was signed.")).to_be_visible()
        assert ctx.store.approval(approval_id)["status"] == "pending"
        step("cancelled a signature: nothing signed, the request still waits")

        page.get_by_role("tab", name="Activity").click()
        expect(page.get_by_text("Your passkey signed").first).to_be_visible(timeout=15_000)
        page.screenshot(path=SHOTS / "dashboard.png", full_page=True)
        browser.close()
    print(f"\nPasskey browser test passed. Screenshots: {SHOTS}")


def step(text: str) -> None:
    print(f"  ok  {text}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
