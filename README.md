# Avaya 9641GS Zadarma Provisioning

Proyecto local para provisionar el Avaya 9641GS contra Zadarma usando:

- `http/`: archivos que descarga el telefono Avaya por HTTP.
- `avaya-shim/avaya_shim.py`: servidor HTTP/PPM y proxy SIP UDP.
- `syslog/syslog_server.py`: colector syslog UDP para logs del telefono.
- `scripts/`: comandos para levantar los servicios en esta Mac.

## Estructura importante

- `http/46xxsettings.txt`: configuracion principal.
- `http/96x1Supgrade.txt`: upgrade/provisioning script Avaya.
- `http/ltm-logo-232x140.jpg`: logo actual.
- `http/d47856b7122c.txt`: login SIP real del telefono. Este archivo esta ignorado por git.
- `http/d47856b7122c.example.txt`: plantilla sin secretos para GitHub.

## Arranque

Levantar HTTP/PPM y syslog:

```sh
/Users/luciogarcia/avaya-zadarma-provisioning/scripts/start_http_ppm.sh
```

Levantar el proxy SIP por `launchctl`:

```sh
/Users/luciogarcia/avaya-zadarma-provisioning/scripts/install_launchctl_sip_shim.sh
```

Detener todo:

```sh
/Users/luciogarcia/avaya-zadarma-provisioning/scripts/stop_all.sh
```

## Logs

- HTTP/PPM: `docker logs avaya-shim`
- Syslog del telefono: `logs/avaya-syslog.log`
- Proxy SIP: `logs/avaya-sip-shim.log`
- Errores del proxy SIP: `logs/avaya-sip-shim.err`

## GitHub

Antes de pushear, verificar:

```sh
git status --ignored
```

El archivo real `http/d47856b7122c.txt` contiene credenciales SIP y debe quedar ignorado.

## Windows sin Docker

Para correrlo en un servidor Windows como exe:

```powershell
.\windows\build-exe.ps1
cd .\dist\AvayaGateway
.\install-startup-task.ps1
```

El instalador deja una Tarea Programada `AvayaGateway` al inicio de Windows, abre firewall para TCP 80, UDP 5060 y UDP 514, y el exe detecta automaticamente la IP local cada vez que arranca.

La UI web queda en:

```text
http://<IP_DEL_WINDOWS>/admin
```

Desde ahi se puede elegir la interfaz de red, ver telefonos registrados y leer logs.

Ver detalles en `windows/README-Windows.md`.
