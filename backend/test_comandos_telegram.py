import unittest
from unittest.mock import Mock, patch

from backend.comandos_telegram import _manejar_boton, _texto_y_botones_detalle


class QuitarStopLossTelegramTests(unittest.TestCase):
    def setUp(self):
        self.posicion = {
            "ticket": 42,
            "simbolo": "EURUSD",
            "tipo": "compra",
            "volumen": 0.1,
            "precio_apertura": 1.1,
            "precio_actual": 1.11,
            "profit": 10,
            "sl": 1.09,
            "tp": 1.12,
        }
        self.obtener_posiciones = Mock(return_value=[self.posicion])
        self.modificar_sl = Mock(return_value={"ok": True})
        self.deshabilitar_be = Mock()

    def _manejar(self, data):
        _manejar_boton(
            {"id": "callback-1", "data": data, "message": {"message_id": 7}},
            "token",
            "chat",
            {},
            {},
            self.obtener_posiciones,
            Mock(),
            Mock(),
            self.modificar_sl,
            Mock(),
            None,
            None,
            self.deshabilitar_be,
        )

    def test_detail_has_remove_sl_button(self):
        _, markup = _texto_y_botones_detalle(self.posicion)
        callbacks = [
            button["callback_data"]
            for row in markup["inline_keyboard"]
            for button in row
        ]

        self.assertIn("quitar_sl:42", callbacks)

    @patch("backend.comandos_telegram._tg")
    def test_remove_sl_requires_confirmation(self, telegram_call):
        self._manejar("quitar_sl:42")

        self.modificar_sl.assert_not_called()
        confirmation = next(
            call.args[2]
            for call in telegram_call.call_args_list
            if call.args[1] == "editMessageText"
        )
        self.assertIn(
            "quitar_sl_si:42",
            confirmation["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
        )
        self.assertIn("sin protección", confirmation["text"])

    @patch("backend.comandos_telegram._tg")
    def test_confirmed_remove_clears_sl_and_disables_automatic_be(self, telegram_call):
        self._manejar("quitar_sl_si:42")

        self.modificar_sl.assert_called_once_with(42, 0.0)
        self.deshabilitar_be.assert_called_once_with(42)
        self.assertTrue(
            any(
                "quedó sin stop loss" in call.args[2].get("text", "")
                for call in telegram_call.call_args_list
                if call.args[1] == "sendMessage"
            )
        )

    @patch("backend.comandos_telegram._tg")
    def test_cannot_remove_sl_twice(self, telegram_call):
        self.posicion["sl"] = 0.0

        self._manejar("quitar_sl_si:42")

        self.modificar_sl.assert_not_called()
        self.deshabilitar_be.assert_not_called()


if __name__ == "__main__":
    unittest.main()
