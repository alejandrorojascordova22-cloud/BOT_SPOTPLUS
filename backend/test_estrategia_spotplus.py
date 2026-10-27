import unittest
from unittest.mock import patch

import pandas as pd

from backend.backtesting import VENTANA_MINIMA, simular_backtest
from backend.estructura import (
    _clasificar_zona_multitemporal,
    _indice_desde_tiempo,
    _patron_reversion_en_cierre,
    _ruptura_y_retroceso_en_micro,
    _seleccionar_zona_micro,
)


class ConfirmacionMicroTests(unittest.TestCase):
    def setUp(self):
        times = pd.date_range("2026-01-01", periods=19, freq="5min")
        self.velas = pd.DataFrame({
            "time": times,
            "open": [9.0] * 19,
            "high": [9.1] * 19,
            "low": [8.9] * 19,
            "close": [9.0] * 19,
        })
        self.velas.loc[16, ["open", "high", "low", "close"]] = [9.5, 10.5, 9.4, 10.4]
        self.velas.loc[17, ["open", "high", "low", "close"]] = [10.3, 10.5, 10.2, 10.3]
        self.velas.loc[18, ["open", "high", "low", "close"]] = [9.0, 9.15, 8.7, 9.1]
        self.pivotes = [{"indice": 10, "precio": 10.0, "tipo": "high"}]
        self.zona = {
            "indice": 11,
            "precio": 9.1,
            "tipo": "compra",
            "limite_inferior": 9.0,
            "limite_superior": 9.2,
            "hora_confirmacion": times[14],
        }

    def test_waits_for_retest_after_bos_and_classifies_atr_strength(self):
        result = _ruptura_y_retroceso_en_micro(
            self.pivotes,
            self.zona,
            self.velas,
            ruptura_cuerpo_atr=1.0,
            ruptura_cierre_atr=0.1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["tipo_ruptura"], "fuerte")
        self.assertEqual(result["indice_ruptura"], 16)
        self.assertEqual(result["precio_actual"], 9.1)
        self.assertEqual(result["patron_vela"], "pinbar")

    def test_waits_for_a_reversal_candle_after_retest(self):
        self.velas.loc[18, ["open", "high", "low", "close"]] = [9.2, 9.2, 8.9, 9.1]

        result = _ruptura_y_retroceso_en_micro(self.pivotes, self.zona, self.velas)

        self.assertIsNone(result)

    def test_does_not_enter_if_live_price_left_the_confirmed_zone(self):
        self.velas.loc[19, ["time", "open", "high", "low", "close"]] = [
            pd.Timestamp.now(tz="UTC").floor("min").tz_localize(None),
            10.2,
            10.4,
            10.1,
            10.3,
        ]

        result = _ruptura_y_retroceso_en_micro(
            self.pivotes, self.zona, self.velas, timeframe_str="M1"
        )

        self.assertIsNone(result)

    def test_detects_bullish_engulfing(self):
        velas = pd.DataFrame({
            "open": [9.4, 9.1],
            "high": [9.5, 9.5],
            "low": [9.0, 9.0],
            "close": [9.2, 9.4],
        })

        self.assertEqual(_patron_reversion_en_cierre(velas, 1, "compra"), "envolvente")

    def test_detects_bearish_engulfing(self):
        velas = pd.DataFrame({
            "open": [9.2, 9.5],
            "high": [9.5, 9.6],
            "low": [9.1, 9.0],
            "close": [9.4, 9.1],
        })

        self.assertEqual(_patron_reversion_en_cierre(velas, 1, "venta"), "envolvente")

    def test_does_not_enter_before_price_returns_to_zone(self):
        self.velas.loc[18, ["open", "high", "low", "close"]] = [10.2, 10.4, 10.1, 10.3]

        result = _ruptura_y_retroceso_en_micro(self.pivotes, self.zona, self.velas)

        self.assertIsNone(result)

    def test_weak_break_is_classified_as_obc(self):
        result = _clasificar_zona_multitemporal(
            self.zona,
            self.pivotes,
            self.velas,
            "M5",
            ruptura_fuerte=False,
            tolerancia_pct=0.15,
        )

        self.assertEqual(result, ("obc", None))

    def test_gets_index_from_utc_time_even_when_timestamps_are_awareness_mixed(self):
        df = pd.DataFrame({
            "time": [
                pd.Timestamp("2026-01-01 00:00:00", tz="UTC"),
                pd.Timestamp("2026-01-01 00:05:00", tz="UTC"),
                pd.Timestamp("2026-01-01 00:10:00", tz="UTC"),
            ],
            "open": [1.0, 1.0, 1.0],
            "high": [1.1, 1.1, 1.1],
            "low": [0.9, 0.9, 0.9],
            "close": [1.0, 1.0, 1.0],
        })

        self.assertEqual(_indice_desde_tiempo(df, "2026-01-01 00:05:00"), 1)
        self.assertEqual(_indice_desde_tiempo(df, pd.Timestamp("2026-01-01 00:05:00", tz="UTC")), 1)

    def test_backtest_replay_keeps_indicator_warmup_candles(self):
        count = VENTANA_MINIMA + 2
        times = pd.date_range("2026-01-01", periods=count, freq="15min")
        closes = [100.0 + index * 0.1 for index in range(count)]
        df = pd.DataFrame({
            "time": times,
            "open": closes,
            "high": [price + 0.2 for price in closes],
            "low": [price - 0.2 for price in closes],
            "close": closes,
        })

        result = simular_backtest(df, config={})

        self.assertEqual(result["indice_inicio_replay"], VENTANA_MINIMA)
        self.assertEqual(len(result["velas"]), count)
        self.assertEqual(result["velas"][0]["time"], int(times[0].timestamp()))

    @patch("backend.estructura._ultima_zona_confirmada_por_tf")
    def test_selects_overlapping_micro_zones_and_reports_confluence(self, ultima_zona):
        zonas = {
            "M1": {"tipo": "compra", "precio": 9.15, "limite_inferior": 9.1, "limite_superior": 9.2},
            "M3": {"tipo": "compra", "precio": 9.19, "limite_inferior": 9.14, "limite_superior": 9.24},
            "M5": {"tipo": "compra", "precio": 9.8, "limite_inferior": 9.75, "limite_superior": 9.85},
        }
        ultima_zona.side_effect = lambda df, tf, tolerancia: (zonas[tf], [])

        result = _seleccionar_zona_micro(
            {"M1": object(), "M3": object(), "M5": object()},
            "compra",
            {"precio": 9.2, "limite_inferior": 9.0, "limite_superior": 9.4},
        )

        self.assertEqual(result["temporalidades"], ["M1", "M3"])
        self.assertEqual(result["confluencias"], 2)
        self.assertEqual(result["timeframe"], "M3")


class BacktestExitReasonTests(unittest.TestCase):
    def _simular_una_operacion(self, vela_salida, sl, tp, be_activo=False):
        count = VENTANA_MINIMA + 2
        times = pd.date_range("2026-01-01", periods=count, freq="15min")
        df = pd.DataFrame({
            "time": times,
            "open": [100.0] * count,
            "high": [100.05] * count,
            "low": [99.95] * count,
            "close": [100.0] * count,
        })
        df.loc[count - 1, ["open", "high", "low", "close"]] = vela_salida
        signal = {"tipo": "compra", "precio_entrada": 100.0, "sl": sl, "tp": tp}

        with patch("backend.backtesting._calcular_senal", side_effect=[signal, None]):
            return simular_backtest(
                df,
                config={"be_activo": be_activo, "be_porcentaje": 0.5},
                contrato=1.0,
            )

    def test_records_stop_loss_and_stop_loss_wins_when_both_levels_touch(self):
        result = self._simular_una_operacion([100.0, 100.3, 99.7, 100.0], 99.8, 100.2)

        self.assertEqual(result["operaciones"][0]["motivo_cierre"], "sl")

    def test_records_take_profit(self):
        result = self._simular_una_operacion([100.0, 100.3, 99.9, 100.1], 99.8, 100.2)

        self.assertEqual(result["operaciones"][0]["motivo_cierre"], "tp")

    def test_records_breakeven_after_stop_moves_to_entry(self):
        result = self._simular_una_operacion(
            [100.0, 100.11, 99.99, 100.0], 99.8, 100.2, be_activo=True
        )

        self.assertEqual(result["operaciones"][0]["motivo_cierre"], "be")
        self.assertTrue(result["operaciones"][0]["be_aplicado"])


class BacktestDiagnosticTests(unittest.TestCase):
    def _velas(self):
        count = VENTANA_MINIMA + 2
        times = pd.date_range("2026-01-01", periods=count, freq="5min")
        prices = [100.0] * count
        return pd.DataFrame({
            "time": times,
            "open": prices,
            "high": [price + 0.05 for price in prices],
            "low": [price - 0.05 for price in prices],
            "close": prices,
        })

    def test_reports_active_strategy_missing_timeframes_and_detected_signals(self):
        df = self._velas()
        signal = {"tipo": "compra", "precio_entrada": 100.0, "sl": 99.0, "tp": 101.0}
        config = {
            "estrategia_biblioteca_activa": "hhh",
            "codigo_estrategia": "senal = 'compra'",
            "codigo_usa_multi_temporalidad": True,
            "timeframe_entrada": "M5",
            "temporalidades_escaneo": ["M1", "M3", "M5", "H1"],
        }

        with patch("backend.backtesting._calcular_senal", return_value=signal):
            result = simular_backtest(
                df,
                config,
                dfs_tf={"M1": df},
            )

        diagnostic = result["diagnostico"]
        self.assertEqual(diagnostic["estrategia_activa"], "hhh")
        self.assertEqual(diagnostic["tipo_estrategia"], "código libre")
        self.assertEqual(diagnostic["timeframes_recibidos"], ["M1", "M5"])
        self.assertEqual(diagnostic["timeframes_faltantes"], ["M3", "H1"])
        self.assertEqual(diagnostic["senales_detectadas"], 1)
        self.assertEqual(diagnostic["entradas_ejecutadas"], 1)

    def test_reports_zero_signals_when_strategy_returns_none(self):
        with patch("backend.backtesting._calcular_senal", return_value=None):
            result = simular_backtest(self._velas(), config={"timeframe_entrada": "M5"})

        diagnostic = result["diagnostico"]
        self.assertEqual(diagnostic["evaluaciones_estrategia"], 2)
        self.assertEqual(diagnostic["senales_detectadas"], 0)
        self.assertEqual(diagnostic["entradas_ejecutadas"], 0)


if __name__ == "__main__":
    unittest.main()
