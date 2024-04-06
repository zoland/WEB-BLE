# BLUA_S.py
#
# BLE UART Class / Sуnc release
#
#
# В Chrome Для Linux и более ранних версий Windows
# 		включить флаг #experimental-web-platform-features в about://flags.
# MTU - Maximum Transfer Unit
#		это максимальная длина SEND-пакета ATT. Значение должно быть между 23 и 200.
#		ESP32 - MAC OS 104 байта
#
# Created 27-mar-2024 by @ZolAnd Altai
# 05-apr-2024	@ZolAnd	- add Chunks release
#

from micropython import const
import bluetooth
import struct
import time

_verbose = True

# Advertising payloads are repeated packets of the following form:
#   1 byte data length (N + 1)
#   1 byte type (see constants below)
#   N bytes type-specific data

_ADV_TYPE_FLAGS 				= const(0x01)

_ADV_TYPE_UUID16_MORE 			= const(0x02)
_ADV_TYPE_UUID16_COMPLETE 		= const(0x03)
_ADV_TYPE_UUID32_MORE 			= const(0x04)
_ADV_TYPE_UUID32_COMPLETE 		= const(0x05)
_ADV_TYPE_UUID128_MORE 			= const(0x06)
_ADV_TYPE_UUID128_COMPLETE 		= const(0x07)

_ADV_TYPE_NAME 					= const(0x09)

_ADV_TYPE_APPEARANCE 			= const(0x19)
_ADV_TYPE_MANUFACTURER 			= const(0xFF)

# adv_type values correspond to the Bluetooth Specification:
_ADV_IND 						= const(0x00)	# connectable and scannable undirected advertising
_ADV_DIRECT_IND 				= const(0x01)	# connectable directed advertising
_ADV_SCAN_IND 					= const(0x02)	# scannable undirected advertising
_ADV_NONCONN_IND				= const(0x03)	# non-connectable undirected advertising
_ADV_SCAN_RSP					= const(0x04)	# scan response

_ADV_MAX_PAYLOAD 				= const(31)

_IRQ_CENTRAL_CONNECT 			= const(1)
_IRQ_CENTRAL_DISCONNECT 		= const(2)
_IRQ_GATTS_WRITE 				= const(3)
_IRQ_GATTS_READ_REQUEST 		= const(4)		# ???

_IRQ_MTU_EXCHANGED 				= const(21)


_FLAG_READ				= const(0x0002)
_FLAG_WRITE_NO_RESPONSE = const(0x0004)
_FLAG_WRITE 			= const(0x0008)
_FLAG_NOTIFY 			= const(0x0010)

# org.bluetooth.characteristic.gap.appearance.xml
_ADV_APPEARANCE_GENERIC_THERMOMETER = const(768)	# значок обычного термометра


_UART_UUID = bluetooth.UUID("80039000-9a6e-44c7-ad11-59f95d85da4c")
_UART_TX = (bluetooth.UUID( "80039001-9a6e-44c7-ad11-59f95d85da4c"),
    _FLAG_READ | _FLAG_NOTIFY,
)
_UART_RX = (bluetooth.UUID( "80039002-9a6e-44c7-ad11-59f95d85da4c"),
    _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE,
)
_UART_SERVICE = (
    _UART_UUID,
    (_UART_TX, _UART_RX),
)


class BLUA:
    
    def __init__(self, name="SM-uart", on_RX=None):
        self.name = name
        self._on_RX = self._echo if on_RX is None else on_RX
        self._max_TX = 20			# MTU
        
        self._rx_flush = False
        self._rx_chunks = b''

        self._connections = set()
        
        def _radio_pl(adv_type, value):
            self._payload += struct.pack("BB", len(value) + 1, adv_type) + value

        # BASE UART ADVERT. PACKAGE
        limited_disc = False			# ???
        br_edr   = False				# Standart bluetooth
        self._payload = bytearray()
        _radio_pl( _ADV_TYPE_FLAGS, struct.pack("B", (0x01 if limited_disc else 0x02) + (0x18 if br_edr else 0x04)))
        _radio_pl( _ADV_TYPE_NAME, name )
        _radio_pl( _ADV_TYPE_UUID128_COMPLETE, bytes(_UART_UUID) )
        #_radio_pl( _ADV_TYPE_APPEARANCE, struct.pack("<h", _ADV_APPEARANCE_GENERIC_THERMOMETER ) )
        #_radio_pl( _ADV_TYPE_MANUFACTURER, struct.pack("<H",free_data) )
        
        if (pl := len(self._payload)) > _ADV_MAX_PAYLOAD:
            raise ValueError(f"Radio: payload in {pl} bytes too large")
        else:
            _verbose and print(f'Radio: actual payload size {pl}')
        
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._irq)
        ((self._handle_tx, self._handle_rx),) = self._ble.gatts_register_services((_UART_SERVICE,))
                
        self._radio(resp='Just Started')


    def _echo(self,value):
        return value
    
    
    def _irq(self, event, data):
        # Track connections so we can send notifications.
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._connections.add(conn_handle)
            #self._on_connect(data)

        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            self._connections.remove(conn_handle)
            #self._on_disconnect(data)
            # Start advertising again to allow a new connection.
            self._radio()

        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            value = self._ble.gatts_read(value_handle)
            if '\n' in value:				# show result
                if self._rx_flush:
                    value = self._rx_chunks + value
                    self._rx_flush = False
                _verbose and print(f"<<<<<<<<<<< READ Last CHUNK: {value}")
                res = self._on_RX(value)
                if res is not None:
                    self.send(res)
            else:
                if not self._rx_flush:			# chunks
                    self._rx_flush = True
                    self._rx_chunks = b''
                    
                self._rx_chunks += value
                _verbose and print(f'<<<<<<<<<<< READ Chunk: {self._rx_chunks}')

        elif event == _IRQ_GATTS_READ_REQUEST:    # A client has issued a read. Note: this is only supported on STM32.
            # Return a non-zero integer to deny the read (see below), or zero (or None) to accept the read.
            conn_handle, attr_handle = data
            _verbose and print(f'IRQ Read request: <{event}>',data )
           
        elif event == _IRQ_MTU_EXCHANGED:    # ATT MTU exchange complete (either initiated by us or the remote device).
            conn_handle, mtu = data
            self._max_TX = mtu-3
            self._ble.config(mtu=512)
            _verbose and print(f'IRQ MTU Exchange: <{conn_handle}> -> MTU:{mtu}' )

        else:
            print(f'IRQ other: <{event}>',data )


    def _radio(self, interval_us=100, resp=None):
        print(f">>> RADIO {'Stopped' if interval_us is None else 'Started'}")
        self._ble.gap_advertise( interval_us, adv_data=self._payload, resp_data=resp )


    def send(self, data=None):
        from time import sleep_ms
        
        #self._max_TX = 20
        for conn_handle in self._connections:
            #data += '\n'
            size = len(data)
            from_ = 0

            while( size > self._max_TX ):
                chunk = data[ from_ : from_+ self._max_TX]
                self._ble.gatts_notify(conn_handle, self._handle_tx, chunk)
                _verbose and print(f'SENT >>>>>>>>>>> {chunk}')
                size -= self._max_TX
                from_ += self._max_TX
                #sleep_ms(150)

            self._ble.gatts_notify(conn_handle, self._handle_tx, data[from_:])
            print(f'SENT last >>>>>>>>>>> {data[from_:]}')
 
    def is_connected(self):
        return len(self._connections) > 0


if __name__ == "__main__":

    def echo(v):
        print(f'ECHO {v}')
        return v


    net = BLUA('ESP32BLE', on_RX=echo)
    
    i = 0
    while True:
        if not net.is_connected(): continue

        # Short burst of queued notifications.
        data = str(i) + "_"
        if i == 5:
            net.send('111111111+222222222_333333333\n')
        i = (i+1)%20
        time.sleep_ms(2000)
