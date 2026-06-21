# Avaya Gateway en Windows

Esto corre el provisionamiento del Avaya sin Docker:

- HTTP/PPM en TCP 80.
- SIP shim en UDP 5060.
- Syslog del telefono en UDP 514.
- UI web de administracion en `/admin`.
- Tarea Programada `AvayaGateway` al inicio de Windows, corriendo como `SYSTEM`.

## 1. Compilar el exe

En el servidor Windows, instalar Python 3 y ejecutar PowerShell:

```powershell
cd C:\ruta\avaya-zadarma-provisioning
.\windows\build-exe.ps1
```

El paquete queda en:

```text
dist\AvayaGateway
```

## 2. Instalar para arranque automatico

Abrir PowerShell como Administrador:

```powershell
cd C:\ruta\avaya-zadarma-provisioning\dist\AvayaGateway
.\install-startup-task.ps1
```

No hace falta hardcodear la IP. El exe detecta la IP local cada vez que arranca y reescribe `46xxsettings.txt` antes de levantar HTTP.

Si el Windows tiene varias placas de red y detecta una IP incorrecta, se puede forzar manualmente:

```powershell
.\install-startup-task.ps1 -ServerIp <IP_DEL_WINDOWS>
```

El instalador:

- copia todo a `C:\AvayaZadarma`;
- genera `C:\AvayaZadarma\.env` en modo automatico;
- deja que `AvayaGateway.exe` actualice `C:\AvayaZadarma\http\46xxsettings.txt` con la IP detectada en cada arranque;
- abre firewall para TCP 80, UDP 5060 y UDP 514;
- crea e inicia la tarea `AvayaGateway`.

## UI web

Abrir desde el servidor o desde una PC de la red:

```text
http://<IP_DEL_WINDOWS>/admin
```

La UI permite:

- elegir la interfaz de red que debe usar el gateway;
- ver la IP publicada al telefono;
- ver telefonos detectados/registrados;
- leer logs del gateway y syslog.

Si se elige una interfaz, se guarda en `.env` como `AVAYA_INTERFACE_ALIAS`. En cada arranque se toma la IP actual de esa interfaz, asi no queda hardcodeada una IP vieja.

## 3. Apuntar el telefono al Windows

El telefono debe descargar el provisioning desde el servidor Windows. Si la IP cambia respecto de la laptop, cambiar el HTTP server del telefono a la IP actual del Windows o entregar esa IP por DHCP/Option 242.

Luego reiniciar el telefono.

## Logs

```text
C:\AvayaZadarma\logs\avaya-gateway.log
C:\AvayaZadarma\logs\avaya-syslog.log
```

## Desinstalar

PowerShell como Administrador:

```powershell
C:\AvayaZadarma\uninstall-startup-task.ps1
```

Para borrar tambien los archivos:

```powershell
C:\AvayaZadarma\uninstall-startup-task.ps1 -RemoveFiles
```
