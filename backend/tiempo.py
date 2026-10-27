import pandas as pd


def timestamps_epoch_segundos(timestamps):
    """Convierte timestamps de pandas a Unix seconds, independiente de su unidad."""
    en_segundos = pd.to_datetime(timestamps, utc=True).dt.as_unit("s")
    return [int(valor) for valor in en_segundos.astype("int64").tolist()]
