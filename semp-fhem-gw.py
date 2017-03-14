#!/usr/bin/env python

import sys, uuid, socket, time, threading, telnetlib, os
from daemonize import Daemonize
from twisted.internet import reactor, task
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.task import LoopingCall
from urlparse import urlparse
from BaseHTTPServer import BaseHTTPRequestHandler,HTTPServer

LOCAL_DOMAIN = 'fritz.box'
SSDP_ADDR = '239.255.255.250'
SSDP_PORT = 1900
HTTP_PORT = 8086
SEARCH_TARGET_GATEWAY ='urn:schemas-simple-energy-management-protocol:device:Gateway:1'
SERVER_TYPE ='Linux/2.6.32 UPnP/1.0 Raspian SSDP Server/1.0.0'
#Unique_UUID = str(uuid.uuid4())
Unique_UUID = 'c85bb1c7-5a3f-4d69-b26d-36173fcb6488'

################# SSDP MESSAGE #################

class SSDP_Messages ():

	notify = ''
	nts = ''

	def __init__ (self, adr, port, location, max_age=1800):
		self.notify = 'NOTIFY * HTTP/1.1\r\n'
		self.notify += 'HOST: {adr}:{port}\r\n'.format(adr=adr, port=port)
		self.notify += 'CACHE-CONTROL: max-age = {:d}\r\n'.format(max_age)
		self.notify += 'SERVER: {}\r\n'.format(SERVER_TYPE)
		self.notify += 'LOCATION: http://{ip}:{port}/uuid:{UUID}/description.xml\r\n'.format(ip=location, port=HTTP_PORT, UUID=Unique_UUID)
		self.nts = 'alive'

	def msg_RootDevice (self):
		r = self.notify
		r += 'NTS: ssdp:{nts}\r\n'.format(nts=self.nts)
		r += 'NT: upnp:rootdevice\r\n'
		r += 'USN: uuid:{UUID}::upnp:rootdevice\r\n \r\n'.format(UUID=Unique_UUID)
		return r

	def msg_DeviceUUID (self):
		r = self.notify
		r += 'NTS: ssdp:{nts}\r\n'.format(nts=self.nts)
		r += 'NT: uuid:{UUID}\r\n'.format(UUID=Unique_UUID)
		r += 'USN: uuid:{UUID}\r\n \r\n'.format(UUID=Unique_UUID)	
		return r
		
	def msg_SEMPGateway (self):
		r = self.notify 
		r += 'NTS: ssdp:{nts}\r\n'.format(nts=self.nts)
		r += 'NT: urn:schemas-simple-energy-management-protocol:device:Gateway:1\r\n' 
		r += 'USN: uuid:{UUID}::urn:schemas-simple-energy-management-protocol:device:Gateway:1\r\n \r\n'.format(UUID=Unique_UUID)
		return r
	
######### SSDP Multicast Client	
		
class SDDP_Multicast_Client(DatagramProtocol):

	def datagramReceived(self, datagram, address):
		lines = datagram.rsplit('\r\n')
		MX=ST=MAN=''
		for l in lines:
			if l[:3] == 'MX:': MX = l[3:].strip()
			if l[:3] == 'ST:': ST = l[3:].strip()
			if l[:4] == 'MAN:': MAN = l[4:].strip()
		adr = address[0]
		port = address[1]
		name = socket.getfqdn(adr)
		
		if (MAN =='"ssdp:discover"') and (ST==SEARCH_TARGET_GATEWAY):
			print "RECIEVING 'discover:semp' from {a}:{p:d} ({n})______".format(a=adr, p=port, n=name, )
			#print (datagram)
			response = self.ssdp_messages.msg_SEMPGateway()
			#print ("SENDING______")
			#print (response)
			self.transport.write(response, (SSDP_ADDR,SSDP_PORT))

		if (MAN =='"ssdp:discover"') and (ST=='ssdp:all'):
			print "RECIEVING 'discover:all' from {a}:{p:d} ({n})______".format(a=adr, p=port, n=name, )
			#print (datagram)
			self.sendHeartBeat()

	def __init__(self, iface):
		self.iface = iface
		self.loop_UDP_Heartbeat = None
		self.ssdp_messages = SSDP_Messages(SSDP_ADDR, SSDP_PORT, iface, 1800)
		self.ssdp = reactor.listenMulticast(SSDP_PORT, self, listenMultiple=True)
		self.ssdp.setLoopbackMode(1)
		print ('Joining SSDP group {} on port {} using interface {}'.format(SSDP_ADDR,SSDP_PORT,iface))
		self.ssdp.joinGroup(SSDP_ADDR, interface=iface)

	def sendHeartBeat(self):
		# This function is called periodically by loop_UDP_Heartbeat 
		# According to SSDP a Controlled Device (here: SEMP gateway) has to send 
		# - one NOTIFY message to announce the so-called root-device (the device itself as it might consist of sub-devices), 
		# - one to announce its device ID and 
		# - one message for every implemented device type and service. 
		# For example if a device implements a device type, it has to send at least three NOTIFY messages.
		for message in [self.ssdp_messages.msg_RootDevice(),\
						self.ssdp_messages.msg_DeviceUUID(),\
						self.ssdp_messages.msg_SEMPGateway()]:
			print ("SENDING______")
			print (message)
			self.transport.write(message, (SSDP_ADDR,SSDP_PORT))
			#time.sleep(0.5)

	def startProtocol(self):
		self.loop_UDP_Heartbeat = LoopingCall(self.sendHeartBeat())
		self.loop_UDP_Heartbeat.start(900, now=False)

	def stop(self):
		self.ssdp_messages.nts='byebye'
		self.sendHeartBeat()
		self.ssdp.leaveGroup(SSDP_ADDR, interface=self.iface)
		print ('Leaving SSDP group {} on port {} using interface {}'.format(SSDP_ADDR,SSDP_PORT,self.iface))
		self.ssdp.stopListening()
		
###############################

class FhemGateWay_HTTP_Handler(BaseHTTPRequestHandler):
	"""
	A HTTP handler that serves the UPnP XML files.
	"""

	# Handler for the GET requests
	def do_GET(self):
	
		parsed = urlparse(self.path)
		from_host = socket.getfqdn(self.client_address[0])
		print ("GET:",parsed," from ",from_host)

		if parsed.path == '/uuid:{uuid}/description.xml'.format(uuid=Unique_UUID):
			content = self.get_UPNP_device_xml().encode()
			self.wfile.write('HTTP/1.1 200 OK\r\nContent-Length: {:d}\r\nConnection: close\r\nContent-Type: text/xml\r\n\r\n'.format(len(content)))
			self.wfile.write(content)
			return
		if parsed.path=="/semp/":
			content = self.get_All_Devices_xml().encode()
			self.wfile.write('HTTP/1.1 200 OK\r\nContent-Length: {:d}\r\nConnection: close\r\nContent-Type: text/xml\r\n\r\n'.format(len(content)))
			self.wfile.write(content)
			return
		if parsed.path=="/semp/DeviceInfo":
			content = self.get_DeviceInfo_Wohnzimmerlampe_xml().encode()
			self.wfile.write('HTTP/1.1 200 OK\r\nContent-Length: {:d}\r\nConnection: close\r\nContent-Type: text/xml\r\n\r\n'.format(len(content)))
			self.wfile.write(content)
			return
		if parsed.path=="/semp/DeviceStatus":
			content = self.get_DeviceStatus_Wohnzimmerlampe_xml().encode()
			self.wfile.write('HTTP/1.1 200 OK\r\nContent-Length: {:d}\r\nConnection: close\r\nContent-Type: text/xml\r\n\r\n'.format(len(content)))
			self.wfile.write(content)
			return
		else:
			self.send_response(404)
			self.send_header('Content-type', 'text/html')
			self.end_headers()
			print ("404 - Not found")
			print (self.path)
			self.wfile.write(b"Not found.")
			return
			
	def do_POST(self):
		self.send_response(200)
		self.end_headers()
		varLen = int(self.headers['Content-Length'])
		postVars = self.rfile.read(varLen)
		print postVars
		return

	def get_UPNP_device_xml(self):
		
		## Get the main device descriptor xml file.
		
		xml = '''<root xmlns="urn:schemas-upnp-org:device-1-0">
	<specVersion>
		<major>1</major>
		<minor>0</minor>
	</specVersion>
	<device>
		<deviceType>urn:schemas-simple-energy-management-protocol:device:Gateway:1</deviceType>
		<friendlyName>{friendly_name}</friendlyName>
		<manufacturer>{manufacturer}</manufacturer>
		<manufacturerURL>{manufacturer_url}</manufacturerURL>
		<modelDescription>{model_description}</modelDescription>
		<modelName>{model_name}</modelName>
		<modelNumber>{model_number}</modelNumber>
		<modelURL>{model_url}</modelURL>
		<serialNumber>{serial_number}</serialNumber>
		<UDN>uuid:{uuid}</UDN>
		<serviceList>
			<service>
				<serviceType>urn:schemas-simple-energy-management-protocol:service:NULL:1:service:NULL:1</serviceType>
				<serviceId>urn:schemas-simple-energy-management-protocol:serviceId:NULL:serviceId:NULL</serviceId>
				<SCPDURL>/XD/NULL.xml</SCPDURL>
				<controlURL>/UD/?0</controlURL>
				<eventSubURL></eventSubURL>
			</service>
		</serviceList>
		<presentationURL>{presentation_url}</presentationURL>
		<semp:X_SEMPSERVICE xmlns:semp="urn:schemas-simple-energy-management-protocol:service-1-0">
		<semp:server>{presentation_url}</semp:server>
		<semp:basePath>/semp</semp:basePath>
		<semp:transport>HTTP/Pull</semp:transport>
		<semp:exchangeFormat>XML</semp:exchangeFormat>
		<semp:wsVersion>1.1.5</semp:wsVersion>
		</semp:X_SEMPSERVICE>
	</device>
</root>
'''
		return xml.format(friendly_name=self.server.friendly_name,
						  manufacturer=self.server.manufacturer,
						  manufacturer_url=self.server.manufacturer_url,
						  model_description=self.server.model_description,
						  model_name=self.server.model_name,
						  model_number=self.server.model_number,
						  model_url=self.server.model_url,
						  serial_number=self.server.serial_number,
						  uuid=self.server.uuid,
						  presentation_url=self.server.presentation_url)
						  
	def fhem_task(self,fcmd):
		tnet_host= "haus.fritz.box"
		tnet_port= 7072
		try:
			tc= telnetlib.Telnet(tnet_host,tnet_port)
			tc.write(fcmd)
			erg= tc.read_until( "\n" )
			tc.close()
		except Exception as e:
			print (e)
		return erg.strip()
		
	def get_All_Devices_xml(self):
		xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Device2EM xmlns="http://www.sma.de/communication/schema/SEMP/v1">
	<DeviceInfo>
		<Identification>
			<DeviceId>F-11223344-112233445500-00</DeviceId>
			<DeviceName>Wohnzimmerlampe</DeviceName>
			<DeviceType>Other</DeviceType>
			<DeviceSerial>08761_0170869</DeviceSerial>
			<DeviceVendor>SvenOtte</DeviceVendor>
		</Identification>
		<Characteristics>
			<MaxPowerConsumption>100</MaxPowerConsumption>
			<MinOnTime>1200</MinOnTime>
			<MinOffTime>600</MinOffTime>
		</Characteristics>
		<Capabilities>
			<CurrentPower>
				<Method>Measurement</Method>
			</CurrentPower>
			<Timestamps>
				<AbsoluteTimestamps>false</AbsoluteTimestamps>
			</Timestamps>
			<Interruptions>
				<InterruptionsAllowed>false</InterruptionsAllowed>
			</Interruptions>
			<Requests>
				<OptionalEnergy>false</OptionalEnergy>
			</Requests>
		</Capabilities>
	</DeviceInfo>
	<DeviceInfo>
		<Identification>
			<DeviceId>F-11223344-112233445501-00</DeviceId>
			<DeviceName>Waeschetrockner</DeviceName>
			<DeviceType>Dryer</DeviceType>
			<DeviceSerial>HM_342513_Sw</DeviceSerial>
			<DeviceVendor>LG</DeviceVendor>
		</Identification>
		<Characteristics>
			<MaxPowerConsumption>900</MaxPowerConsumption>
			<MinOnTime>7200</MinOnTime>
			<MinOffTime>600</MinOffTime>
		</Characteristics>
		<Capabilities>
			<CurrentPower>
				<Method>Measurement</Method>
			</CurrentPower>
			<Timestamps>
				<AbsoluteTimestamps>false</AbsoluteTimestamps>
			</Timestamps>
			<Interruptions>
				<InterruptionsAllowed>false</InterruptionsAllowed>
			</Interruptions>
			<Requests>
				<OptionalEnergy>false</OptionalEnergy>
			</Requests>
		</Capabilities>
	</DeviceInfo>
	<DeviceInfo>
		<Identification>
			<DeviceId>F-11223344-112233445502-00</DeviceId>
			<DeviceName>Waschmaschine</DeviceName>
			<DeviceType>WashingMachine</DeviceType>
			<DeviceSerial>HM_3424E6_Sw</DeviceSerial>
			<DeviceVendor>BOSCH</DeviceVendor>
		</Identification>
		<Characteristics>
			<MaxPowerConsumption>2000</MaxPowerConsumption>
			<MinOnTime>1200</MinOnTime>
			<MinOffTime>600</MinOffTime>
		</Characteristics>
		<Capabilities>
			<CurrentPower>
				<Method>Measurement</Method>
			</CurrentPower>
			<Timestamps>
				<AbsoluteTimestamps>false</AbsoluteTimestamps>
			</Timestamps>
			<Interruptions>
				<InterruptionsAllowed>false</InterruptionsAllowed>
			</Interruptions>
			<Requests>
				<OptionalEnergy>false</OptionalEnergy>
			</Requests>
		</Capabilities>
	</DeviceInfo>
	<DeviceInfo>
		<Identification>
			<DeviceId>F-11223344-112233445503-00</DeviceId>
			<DeviceName>Geschirrspueler</DeviceName>
			<DeviceType>DishWasher</DeviceType>
			<DeviceSerial>08761_0183538</DeviceSerial>
			<DeviceVendor>SvenOtte</DeviceVendor>
		</Identification>
		<Characteristics>
			<MaxPowerConsumption>1700</MaxPowerConsumption>
			<MinOnTime>240</MinOnTime>
			<MinOffTime>600</MinOffTime>
		</Characteristics>
		<Capabilities>
			<CurrentPower>
				<Method>Measurement</Method>
			</CurrentPower>
			<Timestamps>
				<AbsoluteTimestamps>false</AbsoluteTimestamps>
			</Timestamps>
			<Interruptions>
				<InterruptionsAllowed>true</InterruptionsAllowed>
			</Interruptions>
			<Requests>
				<OptionalEnergy>false</OptionalEnergy>
			</Requests>
		</Capabilities>
	</DeviceInfo>
	<DeviceStatus>
		<DeviceId>F-11223344-112233445500-00</DeviceId>
		<EMSignalsAccepted>false</EMSignalsAccepted>
		<Status>{status_Wohnzimmerlampe}</Status>
		<PowerConsumption>
			<PowerInfo>
				<AveragePower>{power_Wohnzimmerlampe}</AveragePower>
				<Timestamp>0</Timestamp>
				<AveragingInterval>60</AveragingInterval>
			</PowerInfo>
		</PowerConsumption>
	</DeviceStatus>
	<DeviceStatus>
		<DeviceId>F-11223344-112233445501-00</DeviceId>
		<EMSignalsAccepted>false</EMSignalsAccepted>
		<Status>{status_Trockner}</Status>
		<PowerConsumption>
			<PowerInfo>
				<AveragePower>{power_Trockner}</AveragePower>
				<Timestamp>0</Timestamp>
				<AveragingInterval>60</AveragingInterval>
			</PowerInfo>
		</PowerConsumption>
	</DeviceStatus>
	<DeviceStatus>
		<DeviceId>F-11223344-112233445502-00</DeviceId>
		<EMSignalsAccepted>false</EMSignalsAccepted>
		<Status>{status_Waschmaschine}</Status>
		<PowerConsumption>
			<PowerInfo>
				<AveragePower>{power_Waschmaschine}</AveragePower>
				<Timestamp>0</Timestamp>
				<AveragingInterval>60</AveragingInterval>
			</PowerInfo>
		</PowerConsumption>
	</DeviceStatus>
	<DeviceStatus>
		<DeviceId>F-11223344-112233445503-00</DeviceId>
		<EMSignalsAccepted>true</EMSignalsAccepted>
		<Status>{status_Geschirrspueler}</Status>
		<PowerConsumption>
			<PowerInfo>
				<AveragePower>{power_Geschirrspueler}</AveragePower>
				<Timestamp>0</Timestamp>
				<AveragingInterval>60</AveragingInterval>
			</PowerInfo>
		</PowerConsumption>
	</DeviceStatus>
</Device2EM>
'''
		
		power_Wohnzimmerlampe = self.fhem_task("{ sprintf('%.0f', [split(' ',ReadingsVal('FBDECT_fbSmartHome_08761_0170869','power',0))]->[0]) }\r\n")
		power_Geschirrspueler = self.fhem_task("{ sprintf('%.0f', [split(' ',ReadingsVal('FBDECT_fbSmartHome_08761_0183538','power',0))]->[0]) }\r\n")
		power_Waschmaschine = self.fhem_task("{sprintf('%.0f', ReadingsVal('HM_3424E6_Pwr','power',0))}\r\n")
		power_Trockner = self.fhem_task("{sprintf('%.0f', ReadingsVal('HM_342513_Pwr','power',0))}\r\n")
		if int(power_Wohnzimmerlampe) > 0: status_Wohnzimmerlampe = 'On'
		else: status_Wohnzimmerlampe = 'Off'
		if int(power_Waschmaschine) > 3: status_Waschmaschine = 'On'
		else: status_Waschmaschine = 'Off'
		if int(power_Trockner) > 3: status_Trockner = 'On'
		else: status_Trockner = 'Off'
		if int(power_Geschirrspueler) > 3: status_Geschirrspueler = 'On'
		else: status_Geschirrspueler = 'Off'
		return xml.format(status_Wohnzimmerlampe=status_Wohnzimmerlampe, 
						  power_Wohnzimmerlampe=power_Wohnzimmerlampe,
						  power_Waschmaschine=power_Waschmaschine,
						  status_Waschmaschine=status_Waschmaschine,
						  power_Trockner=power_Trockner,
						  status_Trockner=status_Trockner,
						  power_Geschirrspueler=power_Geschirrspueler,
						  status_Geschirrspueler=status_Geschirrspueler)

	def get_DeviceInfo_Wohnzimmerlampe_xml(self):
		xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Device2EM xmlns="http://www.sma.de/communication/schema/SEMP/v1">
	<DeviceInfo>
		<Identification>
			<DeviceId>F-11223344-112233445500-00</DeviceId>
			<DeviceName>Wohnzimmerlampe</DeviceName>
			<DeviceType>Other</DeviceType>
			<DeviceSerial>08761_0170869</DeviceSerial>
			<DeviceVendor>SvenOtte</DeviceVendor>
		</Identification>
		<Characteristics>
			<MaxPowerConsumption>100</MaxPowerConsumption>
			<MinOnTime>1200</MinOnTime>
			<MinOffTime>600</MinOffTime>
		</Characteristics>
		<Capabilities>
			<CurrentPower>
				<Method>Measurement</Method>
			</CurrentPower>
			<Timestamps>
				<AbsoluteTimestamps>false</AbsoluteTimestamps>
			</Timestamps>
			<Interruptions>
				<InterruptionsAllowed>false</InterruptionsAllowed>
			</Interruptions>
			<Requests>
				<OptionalEnergy>false</OptionalEnergy>
			</Requests>
		</Capabilities>
	</DeviceInfo>
	<DeviceInfo>
		<Identification>
			<DeviceId>F-11223344-112233445501-01</DeviceId>
			<DeviceName>Waeschetrockner</DeviceName>
			<DeviceType>Dryer</DeviceType>
			<DeviceSerial>HM_342513_Sw</DeviceSerial>
			<DeviceVendor>SvenOtte</DeviceVendor>
		</Identification>
		<Characteristics>
			<MaxPowerConsumption>900</MaxPowerConsumption>
			<MinOnTime>7200</MinOnTime>
			<MinOffTime>600</MinOffTime>
		</Characteristics>
		<Capabilities>
			<CurrentPower>
				<Method>Measurement</Method>
			</CurrentPower>
			<Timestamps>
				<AbsoluteTimestamps>false</AbsoluteTimestamps>
			</Timestamps>
			<Interruptions>
				<InterruptionsAllowed>false</InterruptionsAllowed>
			</Interruptions>
			<Requests>
				<OptionalEnergy>false</OptionalEnergy>
			</Requests>
		</Capabilities>
	</DeviceInfo>
	<DeviceStatus>
		<DeviceId>F-11223344-112233445566-00</DeviceId>
		<EMSignalsAccepted>false</EMSignalsAccepted>
		<Status>{status}</Status>
		<PowerConsumption>
			<PowerInfo>
				<AveragePower>{power}</AveragePower>
				<Timestamp>0</Timestamp>
				<AveragingInterval>60</AveragingInterval>
			</PowerInfo>
		</PowerConsumption>
	</DeviceStatus>
</Device2EM>
'''
		readingsval = self.fhem_task("{ sprintf('%.0f', [split(' ',ReadingsVal('FBDECT_fbSmartHome_08761_0170869','power',0))]->[0]) }\r\n")
		power = int(readingsval)
		if power > 0: status = 'On'
		else: status = 'Off'
		return xml.format(power=power, status=status)

	def get_DeviceStatus_Wohnzimmerlampe_xml(self):
		xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Device2EM xmlns="http://www.sma.de/communication/schema/SEMP/v1">
		<DeviceStatus>
				<DeviceId>F-11223344-112233445566-00</DeviceId>
				<EMSignalsAccepted>false</EMSignalsAccepted>
				<Status>Off</Status>
		</DeviceStatus>
</Device2EM>
'''
		return xml		
		

		
####################################################################

class UPNPHTTPServerBase(HTTPServer):

	#### A simple HTTP server that knows the information about a UPnP device.

	def __init__(self, server_address, request_handler_class):
		HTTPServer.__init__(self, server_address, request_handler_class)
		self.port = None
		self.friendly_name = None
		self.manufacturer = None
		self.manufacturer_url = None
		self.model_description = None
		self.model_name = None
		self.model_url = None
		self.serial_number = None
		self.uuid = None
		self.presentation_url = None

class UPNPHTTPServer(threading.Thread):
	#### A thread that runs UPNPHTTPServerBase.
	
	def __init__(self, port, friendly_name, manufacturer, manufacturer_url, model_description, model_name,
				 model_number, model_url, serial_number, uuid, presentation_url):
		threading.Thread.__init__(self)
		self.server = UPNPHTTPServerBase(('', port), FhemGateWay_HTTP_Handler)
		self.server.port = port
		self.server.friendly_name = friendly_name
		self.server.manufacturer = manufacturer
		self.server.manufacturer_url = manufacturer_url
		self.server.model_description = model_description
		self.server.model_name = model_name
		self.server.model_number = model_number
		self.server.model_url = model_url
		self.server.serial_number = serial_number
		self.server.uuid = uuid
		self.server.presentation_url = presentation_url

	def run(self):
		print ("Starting UPNP Server on port {port}".format(port=self.server.port))
		self.server.serve_forever()
		
################# Main

def reactor_main(iface):
	obj = SDDP_Multicast_Client(iface)
	reactor.addSystemEventTrigger('before', 'shutdown', obj.stop)

def main():
	LOCAL_HOST_NAME = socket.gethostname() + '.' + LOCAL_DOMAIN
	iface = socket.gethostbyname(LOCAL_HOST_NAME)
	print ('UUID: '+Unique_UUID)
	
	http_server = UPNPHTTPServer(HTTP_PORT,
							 friendly_name="SEMP-FHEM-Gateway",
							 manufacturer="Sven Otte",
							 manufacturer_url='http://www.fhem.org/',
							 model_description='FHEM to SMA Home Manager Gateway',
							 model_name="FHEM_SEMP_GW",
							 model_number="1.0.0",
							 model_url="http://www.fhem.org/",
							 serial_number="53-4D-41-53-4D-41",
							 uuid=Unique_UUID,
							 presentation_url="http://{ip}:{port}".format(ip=iface,port=HTTP_PORT))
	http_server.daemon=True
	http_server.start()
	
	reactor.callWhenRunning(reactor_main, iface)
	reactor.run()

if __name__ == "__main__":	
	myname=os.path.basename(sys.argv[0])
	pidfile='/tmp/%s' % myname       # any name
	#daemon = Daemonize(app=myname,pid=pidfile, action=main)
	#daemon.start()
	try:
		main()
	except Exception as e:
		print (e)
		
