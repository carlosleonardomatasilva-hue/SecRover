"""Pruebas sin cámara ni motores; ejecutar python3 -m unittest -v test_basico."""
import asyncio
import unittest
from unittest.mock import patch
from rover_bridge import ALLOWED, RoverBridge, motor_values
from quadmd_telemetry import QuadMDTelemetry

class ComandosTests(unittest.TestCase):
    def test_solo_cuatro_direcciones(self):
        self.assertEqual(ALLOWED, {'FORWARD', 'BACKWARD', 'STRAFE_LEFT', 'STRAFE_RIGHT'})
        self.assertEqual(motor_values('FORWARD', 100), (50,50,50,50))
        self.assertEqual(motor_values('BACKWARD', 100), (-50,-50,-50,-50))
        self.assertEqual(motor_values('STRAFE_LEFT', 100), (50,-50,-50,50))
        self.assertEqual(motor_values('STRAFE_RIGHT', 100), (-50,50,50,-50))
    def test_no_giros_ni_diagonales(self):
        for bad in ('ROTATE_LEFT','ROTATE_RIGHT','DIAG_FRONT_LEFT','DIAG_FRONT_RIGHT','DIAG_BACK_LEFT','DIAG_BACK_RIGHT'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                motor_values(bad, 40)
    def test_decoder_real_sin_serial(self):
        self.assertEqual(QuadMDTelemetry.decode(b'$MSPD:50,-20,0,80#'),
                         ('motor',(5.,-2.,0.,8.)))
        self.assertEqual(QuadMDTelemetry.decode(b'$Battery:11.6V#'),('battery',11.6))

class SimulacionTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulacion_y_estop(self):
        rover = RoverBridge()
        with patch.dict('os.environ', {'ROVER_MODE':'SIMULACION'}):
            await rover.initialize()
        try:
            self.assertIsNone(rover.driver)
            await rover.move('navegador', 'STRAFE_LEFT', 40)
            self.assertEqual(rover.action,'STRAFE_LEFT')
            await rover.stop('navegador')
            self.assertIsNone(rover.action)
            await rover.emergency()
            with self.assertRaises(RuntimeError):
                await rover.move('navegador','FORWARD',40)
            await rover.reset()
            self.assertFalse(rover.estop)
        finally:
            await rover.close()

if __name__ == '__main__':
    unittest.main()
