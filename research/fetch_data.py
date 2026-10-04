from core.config import settings
from core.mt5_client import get_mt5_client 
from pathlib import Path

data_dir = Path(f"{settings.BASE_DIR}/data/{settings.ACCOUNT_BROKER}")
def fetch_data(server: str) -> None:

    mt5_client = get_mt5_client(server)
    try:
        create_data_directory()
        for symbol in settings.SYMBOLS:
            for timeframe in settings.TIMEFRAMES:
                print(f"Fetching {timeframe} data for {symbol} from MT5 server: {server}")
                rates = mt5_client.copy_rates_range(
                    symbol,
                    mt5_client.get_mt5_timeframe(timeframe),
                    settings.START_DATE,
                    settings.END_DATE
                )
                df = convert_to_dataframe(rates)
                df.to_parquet(f"{data_dir}/{symbol}_{timeframe}.parquet", index=False)
                print(f"Saved data for {symbol} {timeframe} to {data_dir}/{symbol}_{timeframe}.parquet")
        print(f"Fetching data from MT5 server: {server}")
    finally:
        mt5_client.disconnect()

def create_data_directory() -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created data directory: {data_dir}")

def convert_to_dataframe(rates):
    import pandas as pd
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df
