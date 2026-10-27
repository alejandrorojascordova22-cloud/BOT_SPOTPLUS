import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import conexion_mt5


class CopiarVelasTests(unittest.TestCase):
    @patch.object(conexion_mt5.mt5, "copy_rates_from_pos")
    @patch.object(conexion_mt5.mt5, "symbol_select")
    def test_selecciona_el_simbolo_antes_de_pedir_velas(self, seleccionar, copiar):
        eventos = []
        seleccionar.side_effect = lambda *args: eventos.append("seleccionar") or True
        copiar.side_effect = lambda *args: eventos.append("copiar") or [{"time": 1}]

        velas = conexion_mt5._copiar_velas_con_reconexion("XAUUSD", 5, 200)

        self.assertEqual(velas, [{"time": 1}])
        self.assertEqual(eventos, ["seleccionar", "copiar"])
        seleccionar.assert_called_once_with("XAUUSD", True)

    @patch.object(conexion_mt5.mt5, "copy_rates_from_pos")
    @patch.object(conexion_mt5.mt5, "symbol_select", return_value=False)
    def test_no_consulta_velas_si_no_puede_seleccionar_el_simbolo(self, seleccionar, copiar):
        with patch.object(conexion_mt5.mt5, "last_error", return_value=(-1, "symbol unavailable")):
            velas = conexion_mt5._copiar_velas_con_reconexion("NOEXISTE", 5, 200)

        self.assertIsNone(velas)
        seleccionar.assert_called_once_with("NOEXISTE", True)
        copiar.assert_not_called()

    @patch.object(conexion_mt5.mt5, "copy_rates_from_pos", return_value=[])
    @patch.object(conexion_mt5.mt5, "symbol_select", return_value=True)
    @patch.object(conexion_mt5.mt5, "last_error", return_value=(1, "Success"))
    def test_trata_una_respuesta_sin_velas_como_fallo(self, last_error, seleccionar, copiar):
        velas = conexion_mt5._copiar_velas_con_reconexion("XAUUSD", 5, 200)

        self.assertIsNone(velas)
        last_error.assert_called_once()
        seleccionar.assert_called_once_with("XAUUSD", True)
        copiar.assert_called_once_with("XAUUSD", 5, 0, 200)


class RetcodeTests(unittest.TestCase):
    @patch.object(
        conexion_mt5.mt5,
        "order_send",
        return_value=SimpleNamespace(retcode=10027, comment="AutoTrading disabled by client"),
    )
    @patch.object(
        conexion_mt5.mt5,
        "symbol_info_tick",
        return_value=SimpleNamespace(ask=100.1, bid=100.0),
    )
    @patch.object(
        conexion_mt5.mt5,
        "symbol_info",
        return_value=SimpleNamespace(
            visible=True,
            digits=2,
            point=0.01,
            trade_stops_level=0,
        ),
    )
    def test_client_disabled_autotrading_returns_actionable_message_without_retries(
        self, simbolo_info, tick, enviar
    ):
        resultado = conexion_mt5.enviar_orden_manual("compra", 0.01, simbolo="XAUUSD")

        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["retcode"], 10027)
        self.assertIn("activa", resultado["error"])
        self.assertIn("Algo Trading", resultado["error"])
        self.assertIn("SpotPlus no puede activar", resultado["error"])
        enviar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
