<img src="assets/jim.gif" alt="sacar una herramienta de la nada" width=35%>

🇬🇧 [English](README.md) · 🇮🇹 [Italiano](README.it.md) · 🇪🇸 Español

# hammerspace

🧰 pequeños scripts que automatizan el trabajo de configuración repetitivo que hago cada vez que pongo en marcha una webapp nueva — dns, hosting, y lo que sea que se gane un sitio aquí con el tiempo.

en la animación, los cómics y los videojuegos, **hammerspace** es ese almacén
extradimensional imaginario del que un personaje saca exactamente la
herramienta que necesita, de la nada, justo en el momento en que la necesita —
[wikipedia cuenta la historia
completa](https://es.wikipedia.org/wiki/Hammerspace), desde los gags de los
looney tunes hasta los fandoms del anime (*urusei yatsura*, *ranma ½*) que
acuñaron el término. [tv
tropes](https://tvtropes.org/pmwiki/pmwiki.php/Main/Hammerspace) tiene un
catálogo más extenso, por si quieres bajar por la madriguera.

la idea aquí es esa: metes la mano, sacas el script adecuado, vuelves a
construir.

## ¿cómo funciona?

un script de python por tarea, que se lanza desde la pestaña actions de github
— sin instalar nada, con las credenciales dentro de los secrets del repo — o
desde una terminal, cuando quieres verlo trabajar.

```mermaid
%%{init: {'theme': 'dark'}}%%
flowchart LR
    YOU[tú] -->|pestaña actions| GHA[github actions]
    YOU -->|terminal| CLI[create_subdomain.py]
    GHA --> CLI
    CLI -->|crea subdominio, escribe .htaccess| UAPI[cpanel uapi]
    CLI -->|borra subdominio y archivos| API2[cpanel api2]
    CLI -.->|autossl, opcional| WHM[api whm]
    CLI -.->|registro a, opcional| NC[api namecheap]
    UAPI --> SRV[(tu servidor)]
    API2 --> SRV
```

la división entre las dos apis de cpanel no es una decisión de estilo: uapi
sencillamente no tiene función de borrado, ni para subdominios ni para
archivos. mira las [notas sobre la api de cpanel](docs/cpanel-api.md).

## herramientas

| herramienta | qué hace |
| --- | --- |
| [`create_subdomain.py`](./create_subdomain.py) | crea un subdominio en cpanel, apunta su document root a `~/<nombre>` y fuerza la redirección https. opcionalmente lanza autossl y crea un registro dns dedicado. también lo borra, con o sin sus archivos. |

## características

- 🚀 **un clic desde la pestaña actions** — sin checkout, sin python local, las credenciales nunca salen de los secrets del repo
- 🧯 **crear y borrar son workflows separados** — una operación destructiva nunca está a una casilla mal marcada de una rutinaria, y el borrado solo recibe las credenciales de cpanel
- ✍️ **para borrar hay que reescribir el nombre** — se comprueba antes incluso de instalar nada
- 🔍 **`--dry-run` en todas partes** — imprime todas las llamadas que haría y de verdad no hace ninguna, ni siquiera de solo lectura
- 🔒 **https forzado** — un bloque `mod_rewrite` escrito en el `.htaccess` del subdominio, añadido a lo que ya hubiera en lugar de reemplazarlo
- 🗑 **archivos a la papelera por defecto** — recuperables desde el file manager de cpanel; `--purge` cuando de verdad lo quieres
- 🛡 **las rutas se leen, no se adivinan** — la document root viene de cpanel mismo, y se rechaza cualquier ruta fuera del home (o que sea `public_html`)
- 🌐 **el dns no suele hacer falta** — un registro comodín creado una sola vez hace que cada subdominio resuelva en cuanto existe
- 💥 **falla pronto y en voz alta** — una credencial que falta se anuncia por su nombre, en vez de convertirse más tarde en un error de api incomprensible

## instalación

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # pon tus credenciales, nunca subas este archivo
chmod 600 .env         # contiene tokens de api reales
```

la lista completa de credenciales, y los secrets que configurar en github, en
[setup](docs/setup.md).

## uso

desde la pestaña **actions**, o bien:

```bash
python3 create_subdomain.py lab                                # crear
python3 create_subdomain.py lab --dry-run                      # ensayarlo
python3 create_subdomain.py lab --delete                       # borra, conserva los archivos
python3 create_subdomain.py lab --delete --with-files          # + carpeta a la papelera
python3 create_subdomain.py lab --delete --with-files --purge  # + carpeta borrada del todo
```

todos los flags, y lo que hace realmente cada paso, en [uso](docs/usage.md).

## estructura del repo

```
create_subdomain.py       la herramienta
test_create_subdomain.py  tests de las funciones puras
.github/workflows/        un workflow para crear, otro para borrar
docs/                     setup, uso, notas de api, resolución de problemas
assets/                   gráficos
```

## documentación

- [setup](docs/setup.md) — credenciales, los dos niveles de token, los secrets de github, quién puede lanzar qué
- [uso](docs/usage.md) — cada flag y cada input de los workflows, y qué hace realmente una ejecución
- [notas sobre la api de cpanel](docs/cpanel-api.md) — los huecos y las sorpresas que costaron tiempo de verdad
- [resolución de problemas](docs/troubleshooting.md) — qué hacer cuando una ejecución se tuerce

## desarrollo

se trabaja en `develop`; los workflows se ejecutan desde `master`.

```bash
pip install -r requirements-dev.txt
pytest -q
```

los tests cubren las funciones puras — validación del nombre, la guarda sobre
la document root, el parseo de respuestas, la construcción de parámetros dns —
así que se ejecutan sin red ni credenciales. todo lo que toca una api se
comprueba con `--dry-run`.

## licencia

[mit](LICENSE)
