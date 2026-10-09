from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"").strip("'")
        os.environ.setdefault(key, value)


@dataclass(slots=True)
class Settings:
    root: Path
    solscan_api_key: str
    pump_profile: str
    early_buyer_limit: int = 50
    funding_trace_depth: int = 3
    call_window_seconds: int = 5
    requests_per_minute: int = 30
    rpc_requests_per_second: float = 9.0
    rpc_history_pages: int = 10
    rpc_transaction_scan_limit: int = 100
    brand_new_seconds: int = 600
    very_fresh_seconds: int = 86400
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    pump_api_url: str = "https://frontend-api-v3.pump.fun"
    pump_swap_api_url: str = "https://swap-api.pump.fun"
    solscan_api_url: str = "https://pro-api.solscan.io/v2.0"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def database_path(self) -> Path:
        return self.root / "analysis.sqlite3"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir / "raw",
            self.data_dir / "cache",
            self.data_dir / "calls",
            self.data_dir / "tokens",
            self.data_dir / "wallets",
            self.output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    analysis_root = Path(os.getenv("ANALYSIS_ROOT", str(ROOT))).expanduser().resolve()
    helius_api_key = os.getenv("HELIUS_API_KEY", "").strip()
    explicit_rpc_url = os.getenv("SOLANA_RPC_URL", "").strip()
    rpc_url = explicit_rpc_url or (
        f"https://mainnet.helius-rpc.com/?api-key={helius_api_key}"
        if helius_api_key
        else "https://api.mainnet-beta.solana.com"
    )
    settings = Settings(
        root=analysis_root,
        solscan_api_key=os.getenv("SOLSCAN_API_KEY", "").strip(),
        pump_profile=os.getenv(
            "PUMP_PROFILE", "6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm"
        ).strip(),
        early_buyer_limit=int(os.getenv("EARLY_BUYER_LIMIT", "50")),
        funding_trace_depth=int(os.getenv("FUNDING_TRACE_DEPTH", "3")),
        call_window_seconds=int(os.getenv("CALL_WINDOW_SECONDS", "5")),
        requests_per_minute=int(os.getenv("SOLSCAN_REQUESTS_PER_MINUTE", "30")),
        rpc_requests_per_second=float(os.getenv("RPC_REQUESTS_PER_SECOND", "9")),
        rpc_history_pages=int(os.getenv("RPC_HISTORY_PAGES", "10")),
        rpc_transaction_scan_limit=int(os.getenv("RPC_TRANSACTION_SCAN_LIMIT", "100")),
        rpc_url=rpc_url,
    )
    settings.ensure_directories()
    return settings
