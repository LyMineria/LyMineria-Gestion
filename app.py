import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import secrets

import pandas as pd
import psycopg2
import streamlit as st

ADMIN_USER = "OcampoElio"


def obtener_conexion():
    """Abre una conexión PostgreSQL fresca por cada uso para evitar conexiones cerradas en re-renders."""
    config = st.secrets.get("database", st.secrets)
    required = ("host", "database", "user", "password", "port")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(
            "Faltan estos secrets de base de datos: " + ", ".join(missing)
        )

    connection = psycopg2.connect(
        host=config["host"],
        database=config["database"],
        user=config["user"],
        password=config["password"],
        port=int(config["port"]),
        sslmode=config.get("sslmode", "require"),
        connect_timeout=10,
    )
    connection.set_client_encoding("UTF8")
    return connection


def preparar_tabla_remitos(connection):
    """Prepara una tabla propia para evitar conflictos con el esquema legacy."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS remitos_app (
                id BIGSERIAL PRIMARY KEY,
                numero_remito BIGINT UNIQUE NOT NULL,
                fecha DATE NOT NULL,
                chofer VARCHAR(150) NOT NULL,
                cantera VARCHAR(200) NOT NULL DEFAULT '',
                camion VARCHAR(30) NOT NULL DEFAULT '',
                batea VARCHAR(30) NOT NULL DEFAULT '',
                toneladas NUMERIC(12, 3) NOT NULL CHECK (toneladas >= 0),
                material VARCHAR(200) NOT NULL,
                tarifa NUMERIC(14, 2) NOT NULL CHECK (tarifa >= 0),
                subtotal NUMERIC(16, 2) NOT NULL CHECK (subtotal >= 0),
                creado_por VARCHAR(150) NOT NULL,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            ALTER TABLE remitos_app
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS numero_remito BIGSERIAL,
                ADD COLUMN IF NOT EXISTS fecha DATE,
                ADD COLUMN IF NOT EXISTS chofer VARCHAR(150),
                ADD COLUMN IF NOT EXISTS cantera VARCHAR(200) DEFAULT '',
                ADD COLUMN IF NOT EXISTS camion VARCHAR(30) DEFAULT '',
                ADD COLUMN IF NOT EXISTS batea VARCHAR(30) DEFAULT '',
                ADD COLUMN IF NOT EXISTS toneladas NUMERIC(12, 3),
                ADD COLUMN IF NOT EXISTS material VARCHAR(200),
                ADD COLUMN IF NOT EXISTS tarifa NUMERIC(14, 2),
                ADD COLUMN IF NOT EXISTS subtotal NUMERIC(16, 2),
                ADD COLUMN IF NOT EXISTS creado_por VARCHAR(150),
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW(),
                ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ DEFAULT NOW()
            """
        )
        cursor.execute("ALTER TABLE remitos_app ALTER COLUMN numero_remito TYPE BIGINT")
        cursor.execute("ALTER TABLE remitos_app ALTER COLUMN cantera SET DEFAULT ''")
        cursor.execute("ALTER TABLE remitos_app ALTER COLUMN camion SET DEFAULT ''")
        cursor.execute("ALTER TABLE remitos_app ALTER COLUMN batea SET DEFAULT ''")
        cursor.execute(
            """
            UPDATE remitos_app
            SET cantera = COALESCE(cantera, ''), camion = COALESCE(camion, ''),
                batea = COALESCE(batea, '')
            WHERE cantera IS NULL OR camion IS NULL OR batea IS NULL
            """
        )
    connection.commit()


def preparar_tabla_usuarios(connection):
    """Crea la base mínima para autenticación y auditoría si aún no existe."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id BIGSERIAL PRIMARY KEY,
                nombre_usuario VARCHAR(150) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                rol VARCHAR(50) NOT NULL DEFAULT 'Operador',
                estado VARCHAR(30) NOT NULL DEFAULT 'Pendiente',
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "ALTER TABLE usuarios ALTER COLUMN password TYPE VARCHAR(255)"
        )
        cursor.execute(
            """
            ALTER TABLE usuarios
                ADD COLUMN IF NOT EXISTS rol VARCHAR(50) DEFAULT 'Operador',
                ADD COLUMN IF NOT EXISTS estado VARCHAR(30) DEFAULT 'Pendiente',
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS auditoria_app (
                id BIGSERIAL PRIMARY KEY,
                usuario VARCHAR(150) NOT NULL,
                accion VARCHAR(100) NOT NULL,
                documento VARCHAR(150) NOT NULL,
                detalle TEXT NOT NULL,
                ocurrido_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    connection.commit()


def preparar_tablas_flota(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS choferes (
                id BIGSERIAL PRIMARY KEY,
                nombre_completo VARCHAR(200) NOT NULL,
                nombre VARCHAR(100) NOT NULL,
                apellido VARCHAR(100) NOT NULL,
                dni VARCHAR(30),
                nro_licencia VARCHAR(50),
                estado VARCHAR(20) DEFAULT 'Activo' CHECK (estado IN ('Activo', 'Vacaciones', 'Licencia')),
                vencimiento_licencia DATE,
                preocupacional DATE,
                cuil VARCHAR(30),
                curso_manejo DATE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS bateas (
                id BIGSERIAL PRIMARY KEY,
                patente VARCHAR(20) NOT NULL,
                capacidad NUMERIC(12, 3) NOT NULL CHECK (capacidad >= 0),
                tipo VARCHAR(100) NOT NULL,
                marca VARCHAR(100) NOT NULL,
                seguro DATE,
                modelo INTEGER,
                service DATE,
                vencimiento_seguro DATE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS camiones (
                id BIGSERIAL PRIMARY KEY,
                itv DATE,
                service DATE,
                patente VARCHAR(20) NOT NULL,
                marca VARCHAR(100) NOT NULL,
                estado VARCHAR(20) NOT NULL CHECK (estado IN ('Roto', 'Funcional', 'Pausa')),
                kilometraje NUMERIC(12, 2) NOT NULL CHECK (kilometraje >= 0),
                control_periodico DATE,
                seguro DATE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS materiales (
                id BIGSERIAL PRIMARY KEY,
                nombre VARCHAR(200) NOT NULL UNIQUE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS canteras (
                id BIGSERIAL PRIMARY KEY,
                nombre VARCHAR(200) NOT NULL UNIQUE,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS facturas (
                id BIGSERIAL PRIMARY KEY,
                cantera_id BIGINT NOT NULL REFERENCES canteras(id) ON DELETE CASCADE,
                nombre VARCHAR(200) NOT NULL,
                fecha DATE NOT NULL,
                total NUMERIC(16, 2) NOT NULL DEFAULT 0,
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS factura_remitos (
                id BIGSERIAL PRIMARY KEY,
                factura_id BIGINT NOT NULL REFERENCES facturas(id) ON DELETE CASCADE,
                remito_id BIGINT NOT NULL REFERENCES remitos_app(id) ON DELETE CASCADE,
                UNIQUE (factura_id, remito_id)
            );
            """
        )
        cursor.execute(
            """
            ALTER TABLE choferes
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS nombre_completo VARCHAR(200),
                ADD COLUMN IF NOT EXISTS nombre VARCHAR(100),
                ADD COLUMN IF NOT EXISTS apellido VARCHAR(100),
                ADD COLUMN IF NOT EXISTS dni VARCHAR(30),
                ADD COLUMN IF NOT EXISTS nro_licencia VARCHAR(50),
                ADD COLUMN IF NOT EXISTS estado VARCHAR(20),
                ADD COLUMN IF NOT EXISTS vencimiento_licencia DATE,
                ADD COLUMN IF NOT EXISTS preocupacional DATE,
                ADD COLUMN IF NOT EXISTS cuil VARCHAR(30),
                ADD COLUMN IF NOT EXISTS curso_manejo DATE,
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            UPDATE choferes
            SET nombre_completo = NULLIF(TRIM(COALESCE(nombre, '') || ' ' || COALESCE(apellido, '')), '')
            WHERE nombre_completo IS NULL;
            ALTER TABLE choferes ALTER COLUMN nombre_completo DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN dni DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN nro_licencia DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN estado DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN curso_manejo TYPE DATE USING NULLIF(curso_manejo::text, '')::DATE;
            ALTER TABLE bateas
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS patente VARCHAR(20),
                ADD COLUMN IF NOT EXISTS capacidad NUMERIC(12, 3),
                ADD COLUMN IF NOT EXISTS tipo VARCHAR(100),
                ADD COLUMN IF NOT EXISTS marca VARCHAR(100),
                ADD COLUMN IF NOT EXISTS seguro DATE,
                ADD COLUMN IF NOT EXISTS modelo INTEGER,
                ADD COLUMN IF NOT EXISTS service DATE,
                ADD COLUMN IF NOT EXISTS vencimiento_seguro DATE,
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            ALTER TABLE bateas ALTER COLUMN seguro TYPE DATE USING NULLIF(seguro::text, '')::DATE;
            ALTER TABLE bateas ALTER COLUMN vencimiento_seguro TYPE DATE USING NULLIF(vencimiento_seguro::text, '')::DATE;
            ALTER TABLE camiones
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS itv DATE,
                ADD COLUMN IF NOT EXISTS service DATE,
                ADD COLUMN IF NOT EXISTS patente VARCHAR(20),
                ADD COLUMN IF NOT EXISTS marca VARCHAR(100),
                ADD COLUMN IF NOT EXISTS estado VARCHAR(20),
                ADD COLUMN IF NOT EXISTS kilometraje NUMERIC(12, 2),
                ADD COLUMN IF NOT EXISTS control_periodico DATE,
                ADD COLUMN IF NOT EXISTS seguro DATE,
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            ALTER TABLE camiones ALTER COLUMN seguro TYPE DATE USING NULLIF(seguro::text, '')::DATE;
            CREATE TABLE IF NOT EXISTS asignaciones_flota (
                id BIGSERIAL PRIMARY KEY,
                camion_id BIGINT,
                batea_id BIGINT,
                chofer_id BIGINT,
                destino VARCHAR(200),
                actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            ALTER TABLE asignaciones_flota
                DROP CONSTRAINT IF EXISTS asignaciones_flota_camion_id_fkey,
                DROP CONSTRAINT IF EXISTS asignaciones_flota_batea_id_fkey,
                DROP CONSTRAINT IF EXISTS asignaciones_flota_chofer_id_fkey;
            CREATE TABLE IF NOT EXISTS auditoria_app (
                id BIGSERIAL PRIMARY KEY,
                usuario VARCHAR(150) NOT NULL,
                accion VARCHAR(100) NOT NULL,
                documento VARCHAR(150) NOT NULL,
                detalle TEXT NOT NULL,
                ocurrido_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
    connection.commit()


def cerrar_sesion():
    st.session_state.pop("usuario_actual", None)
    st.session_state.pop("rol_usuario", None)
    st.rerun()


def registrar_error(accion, detalle):
    errores = st.session_state.setdefault("errores_app", [])
    errores.insert(
        0,
        {
            "momento": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "accion": accion,
            "detalle": str(detalle).splitlines()[0],
        },
    )
    del errores[20:]


def mostrar_error(accion, detalle=None):
    if detalle is not None:
        registrar_error(accion, detalle)
    st.error(f"No se pudo {accion}.")
    st.caption("Abrí el botón ? para consultar el detalle técnico del error.")


def mostrar_panel_errores():
    errores = st.session_state.get("errores_app", [])
    st.subheader("Reporte de errores")
    if not errores:
        st.success("No hay errores registrados en esta sesión.")
        return
    if st.button("Limpiar reporte de errores", key="limpiar_errores"):
        st.session_state.errores_app = []
        st.rerun()
    for error in errores:
        st.error(f"{error['momento']} | {error['accion']}")
        st.code(error["detalle"])


def registrar_auditoria(cursor, accion, documento, detalle):
    cursor.execute(
        """
        INSERT INTO auditoria_app (usuario, accion, documento, detalle)
        VALUES (%s, %s, %s, %s)
        """,
        (
            st.session_state.get("usuario_actual", "sistema"),
            accion,
            documento,
            detalle,
        ),
    )


@st.cache_data(ttl=10)
def cargar_auditoria():
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            """
            SELECT ocurrido_en, usuario, accion, documento, detalle
            FROM auditoria_app
            ORDER BY ocurrido_en DESC, id DESC
            LIMIT 500
            """,
            connection,
        )
    finally:
        connection.close()


def mostrar_panel_auditoria():
    st.subheader("Historial de cambios")
    try:
        auditoria = cargar_auditoria()
    except Exception as error:
        mostrar_error("cargar el historial de cambios", error)
        return
    if auditoria.empty:
        st.info("Todavía no hay cambios registrados.")
        return
    auditoria = auditoria.rename(
        columns={
            "ocurrido_en": "Fecha y hora",
            "usuario": "Usuario",
            "accion": "Acción",
            "documento": "Documento",
            "detalle": "Detalle",
        }
    )
    st.dataframe(auditoria, use_container_width=True, hide_index=True)


@st.cache_data(ttl=30)
def cargar_opciones_distintas(tabla, columna):
    tablas_permitidas = {"remitos_app", "choferes", "bateas", "camiones"}
    columnas_permitidas = {
        "cantera", "camion", "batea", "nombre_completo", "patente", "marca", "tipo"
    }
    if tabla not in tablas_permitidas or columna not in columnas_permitidas:
        raise ValueError("Origen de opciones no permitido.")
    connection = obtener_conexion()
    try:
        datos = pd.read_sql_query(
            f"SELECT DISTINCT {columna} FROM {tabla} "
            f"WHERE {columna} IS NOT NULL AND TRIM({columna}::text) <> '' "
            f"ORDER BY {columna}",
            connection,
        )
        return datos[columna].astype(str).tolist()
    finally:
        connection.close()


@st.cache_data(ttl=30)
def cargar_canteras():
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            "SELECT id, nombre FROM canteras ORDER BY nombre",
            connection,
        )
    finally:
        connection.close()


@st.cache_data(ttl=30)
def cargar_materiales():
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            "SELECT id, nombre FROM materiales ORDER BY nombre",
            connection,
        )
    finally:
        connection.close()


def guardar_cantera(nombre):
    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        raise ValueError("La cantera no puede estar vacía.")
    connection = obtener_conexion()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO canteras (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                (nombre_limpio,),
            )
        connection.commit()
    finally:
        connection.close()
    st.cache_data.clear()


def guardar_material(nombre):
    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        raise ValueError("El material no puede estar vacío.")
    connection = obtener_conexion()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO materiales (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                (nombre_limpio,),
            )
        connection.commit()
    finally:
        connection.close()
    st.cache_data.clear()


@st.cache_data(ttl=30)
def obtener_facturas_por_cantera(cantera_id):
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            """
            SELECT f.id, f.nombre, f.fecha,
                   COUNT(fr.remito_id) AS remitos,
                   COALESCE(SUM(r.subtotal), 0) AS total
            FROM facturas f
            LEFT JOIN factura_remitos fr ON fr.factura_id = f.id
            LEFT JOIN remitos_app r ON r.id = fr.remito_id
            WHERE f.cantera_id = %s
            GROUP BY f.id, f.nombre, f.fecha
            ORDER BY f.fecha DESC, f.id DESC
            """,
            connection,
            params=(int(cantera_id),),
        )
    finally:
        connection.close()


@st.cache_data(ttl=30)
def obtener_remitos_facturacion(cantera_nombre):
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            """
            SELECT id, numero_remito, fecha, chofer, material, subtotal
            FROM remitos_app
            WHERE cantera = %s
            ORDER BY fecha DESC, id DESC
            """,
            connection,
            params=(cantera_nombre,),
        )
    finally:
        connection.close()


def crear_factura(cantera_id, nombre, fecha, remitos_ids):
    connection = obtener_conexion()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO facturas (cantera_id, nombre, fecha) VALUES (%s, %s, %s) RETURNING id",
                (int(cantera_id), nombre.strip(), fecha),
            )
            factura_id = cursor.fetchone()[0]
            for remito_id in remitos_ids:
                cursor.execute(
                    "INSERT INTO factura_remitos (factura_id, remito_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (factura_id, int(remito_id)),
                )
        connection.commit()
    finally:
        connection.close()


def selector_catalogo(label, opciones, valor_actual, key, incluir_todos=False):
    valores = [str(item).strip() for item in (opciones or []) if str(item).strip()]
    if valor_actual and str(valor_actual).strip() and str(valor_actual).strip() not in valores:
        valores.insert(0, str(valor_actual).strip())
    opciones_finales = ["Todos"] + valores if incluir_todos else valores
    if not opciones_finales:
        return ""
    valor_base = str(valor_actual).strip() if valor_actual else ""
    indice = opciones_finales.index(valor_base) if valor_base in opciones_finales else 0
    return st.selectbox(label, opciones_finales, index=indice, key=key)


def fecha_hoy():
    return datetime.datetime.now(datetime.timezone.utc).date()


def render_campo_catalogo(label, opciones, valor_actual="", key_prefix="catalogo", permitir_nuevo=True):
    valores = [str(item).strip() for item in (opciones or []) if str(item).strip()]
    valor_actual = str(valor_actual).strip() if valor_actual else ""
    if valor_actual and valor_actual not in valores:
        valores.insert(0, valor_actual)
    opciones_visibles = (["Nuevo..."] + valores) if permitir_nuevo else valores
    if not opciones_visibles:
        return ""
    indice = (
        valores.index(valor_actual) + 1
        if permitir_nuevo and valor_actual and valor_actual in valores
        else 0
    )
    seleccionado = st.selectbox(label, opciones_visibles, index=indice, key=f"{key_prefix}_select")
    if permitir_nuevo and seleccionado == "Nuevo...":
        valor_nuevo = st.text_input(f"{label} (nuevo)", key=f"{key_prefix}_nuevo")
        return (valor_nuevo or "").strip()
    return str(seleccionado).strip()


def construir_opciones_filtro(series):
    valores = sorted(
        {str(valor).strip() for valor in series.dropna() if str(valor).strip()},
        key=lambda item: item.lower(),
    )
    return ["Todos"] + valores


def aplicar_filtros_remitos(remitos, filtros):
    if remitos.empty:
        return remitos.copy()

    vista = remitos.copy()
    vista["fecha"] = pd.to_datetime(vista["fecha"], errors="coerce")
    vista = vista[vista["fecha"].notna()]

    if filtros.get("anio") and filtros["anio"] != "Todos":
        vista = vista[vista["fecha"].dt.year.astype(str) == str(filtros["anio"])]

    if filtros.get("mes") and filtros["mes"] != "Todos":
        mes_numero = list(pd.date_range("2000-01-01", periods=12, freq="MS")).index(
            pd.Timestamp(year=2000, month=list(pd.date_range("2000-01-01", periods=12, freq="MS")).index(
                pd.Timestamp(year=2000, month=1)
            ) + 1, day=1)
        )
        nombre_mes = filtros["mes"]
        for idx, nombre in enumerate(pd.date_range("2000-01-01", periods=12, freq="MS").strftime("%B"), start=1):
            if nombre == nombre_mes:
                mes_numero = idx
                break
        vista = vista[vista["fecha"].dt.month == mes_numero]

    for campo, valor in (
        ("cantera", filtros.get("cantera")),
        ("material", filtros.get("material")),
        ("chofer", filtros.get("chofer")),
        ("batea", filtros.get("patente")),
        ("camion", filtros.get("camion")),
    ):
        if valor and valor != "Todos":
            vista = vista[vista[campo].astype(str).str.strip() == str(valor).strip()]

    return vista.sort_values(["fecha", "id"], ascending=[False, False]).reset_index(drop=True)


def generar_hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verificar_password(password, stored_password):
    if not stored_password:
        return False, False
    if not stored_password.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(password, stored_password), True
    try:
        _, iterations, salt_hex, digest_hex = stored_password.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex), False
    except (ValueError, TypeError):
        return False, False


def decimal_positivo(value, nombre):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{nombre} debe ser un número válido.") from None
    if number < 0:
        raise ValueError(f"{nombre} no puede ser negativo.")
    return number


@st.cache_data(ttl=10)
def cargar_remitos():
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            """
                                 SELECT id, numero_remito, fecha, chofer, cantera, camion, batea,
                                     toneladas, material, tarifa, subtotal,
                   creado_por, creado_en
                 FROM remitos_app
            ORDER BY fecha DESC, id DESC
            """,
            connection,
        )
    finally:
        connection.close()


@st.cache_data(ttl=30)
def cargar_choferes_activos():
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            """
            SELECT id, nombre, apellido, estado
            FROM choferes
            WHERE estado = 'Activo'
            ORDER BY apellido, nombre
            """,
            connection,
        )
    finally:
        connection.close()


@st.cache_data(ttl=30)
def cargar_flota(tabla):
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(f"SELECT * FROM {tabla} ORDER BY id", connection)
    finally:
        connection.close()


def editar_recursos_flota(tabla, columnas, etiqueta):
    connection = obtener_conexion()
    try:
        datos = pd.read_sql_query(
            f"SELECT id, {', '.join(columnas)} FROM {tabla} ORDER BY id",
            connection,
        )
    finally:
        connection.close()
    if datos.empty:
        st.info(f"No hay {etiqueta} cargados.")
        return

    consulta = st.text_input(
        f"Buscar {etiqueta[:-1]}", key=f"buscar_{tabla}",
        placeholder="Escribí nombre, patente, DNI o marca...",
    ).strip().lower()
    visibles = datos
    if consulta:
        mascara = datos.astype(str).apply(
            lambda columna: columna.str.lower().str.contains(consulta, na=False)
        ).any(axis=1)
        visibles = datos[mascara]
    if visibles.empty:
        st.info("No se encontraron recursos con esa búsqueda.")
        return

    editados = st.data_editor(
        visibles,
        key=f"editor_{tabla}",
        use_container_width=True,
        hide_index=True,
        disabled=["id"],
    )
    if st.button(f"Guardar cambios de {etiqueta}", key=f"guardar_edicion_{tabla}"):
        connection = obtener_conexion()
        try:
            with connection.cursor() as cursor:
                assignments = ", ".join(f"{columna} = %s" for columna in columnas)
                for _, fila in editados.iterrows():
                    valores = [
                        None if pd.isna(fila[columna]) else fila[columna]
                        for columna in columnas
                    ]
                    cursor.execute(
                        f"UPDATE {tabla} SET {assignments} WHERE id = %s",
                        valores + [int(fila["id"])],
                    )
                    registrar_auditoria(
                        cursor,
                        "Editar flota",
                        f"{etiqueta} #{int(fila['id'])}",
                        f"Se editaron los datos de {etiqueta[:-1]}.",
                    )
            connection.commit()
            with connection.cursor() as cursor:
                registrar_auditoria(
                    cursor,
                    "Editar asignaciones",
                    "Cuadro de asignación operativa",
                    "Se actualizaron las relaciones entre camiones, bateas, choferes y destinos.",
                )
            connection.commit()
            connection.close()
            st.success("Cambios guardados.")
            st.rerun()
        except Exception as error:
            connection.rollback()
            connection.close()
            mostrar_error(f"guardar cambios de {etiqueta}", error)


def mostrar_cuadro_asignaciones():
    st.subheader("Asignación operativa")
    st.caption("Elegí qué camión, batea, chofer y destino trabajan juntos. Podés eliminar filas completas si ya no hace falta.")
    try:
        camiones = cargar_flota("camiones")
        bateas = cargar_flota("bateas")
        choferes = cargar_flota("choferes")
        connection = obtener_conexion()
        asignaciones_guardadas = pd.read_sql_query(
            "SELECT camion_id, batea_id, chofer_id, destino FROM asignaciones_flota ORDER BY id",
            connection,
        )
        connection.close()
    except Exception as error:
        mostrar_error("cargar datos para asignaciones", error)
        return

    camion_opciones = {"Sin asignar": None}
    camion_opciones.update({str(fila.patente): int(fila.id) for fila in camiones.itertuples()})
    batea_opciones = {"Sin asignar": None}
    batea_opciones.update({str(fila.patente): int(fila.id) for fila in bateas.itertuples()})
    chofer_opciones = {"Sin asignar": None}
    chofer_opciones.update({f"{fila.nombre} {fila.apellido}": int(fila.id) for fila in choferes.itertuples()})
    cantidad_filas = max(len(camiones), len(bateas), len(choferes), 1)
    with st.form("form_asignaciones"):
        encabezado_camion, encabezado_batea, encabezado_chofer, encabezado_destino, encabezado_borrar = st.columns(5)
        with encabezado_camion:
            st.caption("Camión")
        with encabezado_batea:
            st.caption("Batea")
        with encabezado_chofer:
            st.caption("Chofer")
        with encabezado_destino:
            st.caption("Destino")
        with encabezado_borrar:
            st.caption("Quitar")

        asignaciones = []
        for indice in range(cantidad_filas):
            col_camion, col_batea, col_chofer, col_destino, col_borrar = st.columns(5)
            with col_borrar:
                quitar_fila = st.checkbox("Quitar", key=f"asig_quitar_{indice}", value=False)
            if quitar_fila:
                asignaciones.append(None)
                continue
            with col_camion:
                camion_guardado = (
                    asignaciones_guardadas.iloc[indice]["camion_id"]
                    if indice < len(asignaciones_guardadas)
                    else None
                )
                camion_index = next(
                    (pos for pos, valor in enumerate(camion_opciones.values()) if valor == camion_guardado),
                    0,
                )
                camion = st.selectbox(
                    "",
                    list(camion_opciones),
                    index=camion_index,
                    key=f"asig_camion_{indice}",
                    label_visibility="collapsed",
                )
            with col_batea:
                batea_guardada = (
                    asignaciones_guardadas.iloc[indice]["batea_id"]
                    if indice < len(asignaciones_guardadas)
                    else None
                )
                batea_index = next(
                    (pos for pos, valor in enumerate(batea_opciones.values()) if valor == batea_guardada),
                    0,
                )
                batea = st.selectbox(
                    "",
                    list(batea_opciones),
                    index=batea_index,
                    key=f"asig_batea_{indice}",
                    label_visibility="collapsed",
                )
            with col_chofer:
                chofer_guardado = (
                    asignaciones_guardadas.iloc[indice]["chofer_id"]
                    if indice < len(asignaciones_guardadas)
                    else None
                )
                chofer_index = next(
                    (pos for pos, valor in enumerate(chofer_opciones.values()) if valor == chofer_guardado),
                    0,
                )
                chofer = st.selectbox(
                    "",
                    list(chofer_opciones),
                    index=chofer_index,
                    key=f"asig_chofer_{indice}",
                    label_visibility="collapsed",
                )
            with col_destino:
                destino_guardado = (
                    asignaciones_guardadas.iloc[indice]["destino"]
                    if indice < len(asignaciones_guardadas)
                    else ""
                )
                destino = st.text_input(
                    "",
                    value=destino_guardado or "",
                    key=f"asig_destino_{indice}",
                    label_visibility="collapsed",
                )
            asignaciones.append(
                (
                    camion_opciones[camion],
                    batea_opciones[batea],
                    chofer_opciones[chofer],
                    destino.strip(),
                )
            )
        guardar = st.form_submit_button("Guardar asignaciones", type="primary")

    if guardar:
        try:
            connection = obtener_conexion()
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM asignaciones_flota")
                for fila in asignaciones:
                    if fila is None:
                        continue
                    camion_id, batea_id, chofer_id, destino = fila
                    if any([camion_id, batea_id, chofer_id, destino]):
                        cursor.execute(
                            """
                            INSERT INTO asignaciones_flota
                                (camion_id, batea_id, chofer_id, destino)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (camion_id, batea_id, chofer_id, destino or None),
                        )
            connection.commit()
            connection.close()
            st.success("Asignaciones actualizadas.")
            st.rerun()
        except Exception as error:
            connection.rollback()
            connection.close()
            mostrar_error("guardar asignaciones", error)


def mostrar_formulario_flota(tipo):
    st.subheader(f"Agregar {tipo}")
    with st.form(f"form_flota_{tipo.lower()}"):
        try:
            recursos_existentes = cargar_flota(
                {"Chofer": "choferes", "Batea": "bateas", "Camión": "camiones"}[tipo]
            )
        except Exception:
            recursos_existentes = pd.DataFrame()

        if tipo == "Chofer":
            etiquetas = ["Completar manualmente"] + (
                recursos_existentes["nombre_completo"].fillna("").astype(str).tolist()
                if not recursos_existentes.empty else []
            )
            fuente = st.selectbox("Autocompletar desde un chofer", etiquetas)
            fila_fuente = (
                recursos_existentes.loc[
                    recursos_existentes["nombre_completo"].astype(str) == fuente
                ].iloc[0]
                if fuente != "Completar manualmente" else None
            )
            nombre = st.text_input("Nombre", value=str(fila_fuente["nombre"]) if fila_fuente is not None else "")
            apellido = st.text_input("Apellido", value=str(fila_fuente["apellido"]) if fila_fuente is not None else "")
            dni = st.text_input("DNI", value=str(fila_fuente["dni"]) if fila_fuente is not None and pd.notna(fila_fuente["dni"]) else "")
            licencia = st.text_input("Nro. licencia", value=str(fila_fuente["nro_licencia"]) if fila_fuente is not None and pd.notna(fila_fuente["nro_licencia"]) else "")
            estado = st.selectbox("Estado", ["Activo", "Vacaciones", "Licencia"])
            vencimiento = st.date_input("Vencimiento licencia", value=fecha_hoy())
            preocupacional = st.date_input("Preocupacional", value=fecha_hoy())
            curso = st.date_input(
                "Curso de manejo",
                value=(
                    pd.to_datetime(fila_fuente["curso_manejo"], errors="coerce").date()
                    if fila_fuente is not None and pd.notna(fila_fuente["curso_manejo"])
                    else fecha_hoy()
                ),
            )
        elif tipo == "Batea":
            etiquetas = ["Completar manualmente"] + (
                recursos_existentes["patente"].astype(str).tolist()
                if not recursos_existentes.empty else []
            )
            fuente = st.selectbox("Autocompletar desde una batea", etiquetas)
            fila_fuente = (
                recursos_existentes.loc[
                    recursos_existentes["patente"].astype(str) == fuente
                ].iloc[0]
                if fuente != "Completar manualmente" else None
            )
            patente = st.text_input("Patente", value=str(fila_fuente["patente"]) if fila_fuente is not None else "")
            capacidad = st.number_input("Capacidad (toneladas)", min_value=0.0, step=0.001)
            tipo_batea = st.text_input("Tipo", value=str(fila_fuente["tipo"]) if fila_fuente is not None else "")
            marca = st.text_input("Marca", value=str(fila_fuente["marca"]) if fila_fuente is not None else "")
            vencimiento_seguro = st.date_input("Vencimiento seguro", value=fecha_hoy())
            modelo = st.number_input("Modelo (año)", min_value=1900, max_value=2100, value=2026)
            service = st.date_input("Service", value=fecha_hoy())
        else:
            etiquetas = ["Completar manualmente"] + (
                recursos_existentes["patente"].astype(str).tolist()
                if not recursos_existentes.empty else []
            )
            fuente = st.selectbox("Autocompletar desde un camión", etiquetas)
            fila_fuente = (
                recursos_existentes.loc[
                    recursos_existentes["patente"].astype(str) == fuente
                ].iloc[0]
                if fuente != "Completar manualmente" else None
            )
            itv = st.date_input("ITV", value=fecha_hoy())
            service = st.date_input("Service", value=fecha_hoy())
            patente = st.text_input("Patente", value=str(fila_fuente["patente"]) if fila_fuente is not None else "")
            marca = st.text_input("Marca", value=str(fila_fuente["marca"]) if fila_fuente is not None else "")
            estado = st.selectbox("Estado", ["Roto", "Funcional", "Pausa"])
            kilometraje = st.number_input("Kilometraje", min_value=0.0, step=1.0)
            control = st.date_input("Control periódico", value=fecha_hoy())
            seguro = st.date_input(
                "Seguro",
                value=(
                    pd.to_datetime(fila_fuente["seguro"], errors="coerce").date()
                    if fila_fuente is not None and pd.notna(fila_fuente["seguro"])
                    else fecha_hoy()
                ),
            )

        guardar = st.form_submit_button("Guardar recurso", type="primary")

    if not guardar:
        return

    try:
        connection = obtener_conexion()
        with connection.cursor() as cursor:
            if tipo == "Chofer":
                if not nombre.strip() or not apellido.strip():
                    st.warning("Completá nombre y apellido.")
                    connection.close()
                    return
                cursor.execute(
                    """
                    INSERT INTO choferes
                        (nombre_completo, nombre, apellido, dni, nro_licencia, estado,
                         vencimiento_licencia, preocupacional, curso_manejo)
                        VALUES (%s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s,
                            %s, %s, %s)
                    """,
                    (
                        f"{nombre.strip()} {apellido.strip()}",
                        nombre.strip(),
                        apellido.strip(),
                        dni.strip(),
                        licencia.strip(),
                        estado,
                        vencimiento,
                        preocupacional,
                        curso,
                    ),
                )
            elif tipo == "Batea":
                if not all([patente.strip(), tipo_batea.strip(), marca.strip()]):
                    st.warning("Completá patente, tipo y marca.")
                    connection.close()
                    return
                cursor.execute(
                    """
                    INSERT INTO bateas
                    (patente, capacidad, tipo, marca, vencimiento_seguro, modelo, service)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (patente.strip(), capacidad, tipo_batea.strip(), marca.strip(),
                     vencimiento_seguro, modelo, service),
                )
            else:
                if not all([patente.strip(), marca.strip()]):
                    st.warning("Completá patente y marca.")
                    connection.close()
                    return
                cursor.execute(
                    """
                    INSERT INTO camiones
                    (itv, service, patente, marca, estado, kilometraje,
                     control_periodico, seguro)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (itv, service, patente.strip(), marca.strip(), estado,
                     kilometraje, control, seguro),
                )
        connection.commit()
        with connection.cursor() as cursor:
            registrar_auditoria(
                cursor,
                "Crear flota",
                f"{tipo} {patente if tipo != 'Chofer' else nombre + ' ' + apellido}",
                f"Se agregó un nuevo recurso de tipo {tipo}.",
            )
        connection.commit()
        connection.close()
        st.session_state.mostrar_formulario_flota = False
        st.success(f"{tipo} guardado correctamente.")
        st.rerun()
    except Exception as error:
        st.error(f"No se pudo guardar el {tipo.lower()}.")
        st.code(str(error).splitlines()[0])
        registrar_error(f"guardar el {tipo.lower()}", error)


def formulario_remito(remito=None):
    editando = remito is not None
    identificador = str(remito["id"]) if editando else "nuevo"
    st.subheader("Editar remito" if editando else "Cargar remito")

    with st.form(f"form_remito_{identificador}"):
        try:
            choferes = cargar_choferes_activos()
            canteras = cargar_canteras()["nombre"].astype(str).tolist()
            materiales = cargar_materiales()["nombre"].astype(str).tolist()
            camiones = cargar_flota("camiones")["patente"].astype(str).tolist()
            bateas = cargar_flota("bateas")["patente"].astype(str).tolist()
            connection = obtener_conexion()
            asignaciones_chofer = pd.read_sql_query(
                """
                SELECT ch.nombre_completo, ch.nombre, ch.apellido,
                       cam.patente as camion, bat.patente as batea, a.destino
                FROM choferes ch
                LEFT JOIN asignaciones_flota a ON a.chofer_id = ch.id
                LEFT JOIN camiones cam ON cam.id = a.camion_id
                LEFT JOIN bateas bat ON bat.id = a.batea_id
                ORDER BY ch.nombre_completo
                """,
                connection,
            )
            connection.close()
        except Exception as error:  # noqa: BLE001
            st.error("No se pudieron cargar los datos del remito.")
            registrar_error("cargar datos del remito", error)
            st.code(str(error).splitlines()[0])
            return
        if choferes.empty:
            st.warning("Primero cargá un chofer con estado Activo en Flota.")
            return

        chofer_valor = str(remito["chofer"]).strip() if editando else ""
        chofer = st.text_input(
            "Chofer",
            value=chofer_valor,
            placeholder="Escribí el nombre y se completará con la flota si existe",
            key=f"remito_chofer_input_{identificador}",
        )

        asociacion_chofer = pd.DataFrame()
        if chofer.strip():
            alias = chofer.strip().lower()
            asociacion_chofer = asignaciones_chofer[
                (asignaciones_chofer["nombre_completo"].astype(str).str.strip().str.lower() == alias)
                | (asignaciones_chofer["nombre"].astype(str).str.strip().str.lower() == alias)
                | (asignaciones_chofer["apellido"].astype(str).str.strip().str.lower() == alias)
            ]
        camion_default = remito.get("camion", "") if editando else (
            asociacion_chofer["camion"].dropna().astype(str).iloc[0]
            if not asociacion_chofer.empty and pd.notna(asociacion_chofer["camion"]).any()
            else ""
        )
        batea_default = remito.get("batea", "") if editando else (
            asociacion_chofer["batea"].dropna().astype(str).iloc[0]
            if not asociacion_chofer.empty and pd.notna(asociacion_chofer["batea"]).any()
            else ""
        )
        cantera_default = remito.get("cantera", "") if editando else (
            asociacion_chofer["destino"].dropna().astype(str).iloc[0]
            if not asociacion_chofer.empty and pd.notna(asociacion_chofer["destino"]).any()
            else ""
        )
        if not editando and asociacion_chofer.empty:
            st.caption("Si el chofer existe en la flota y tiene asignación cargada, se completarán camión, batea y cantera.")

        col_numero, col_chofer = st.columns(2)
        with col_numero:
            numero_remito = st.text_input(
                "Número de remito",
                value=str(remito["numero_remito"]) if editando else "",
                help="Ingresá el número real del remito; puede ser largo y no se genera automáticamente.",
            )
        with col_chofer:
            st.caption("")

        col_fecha, col_cantera = st.columns(2)
        with col_fecha:
            fecha = st.date_input(
                "Fecha",
                value=pd.to_datetime(remito["fecha"]).date() if editando else fecha_hoy(),
            )
        with col_cantera:
            cantera = render_campo_catalogo(
                "Cantera",
                canteras,
                valor_actual=cantera_default if not editando else remito.get("cantera", ""),
                key_prefix=f"remito_cantera_{identificador}",
            )
            if not cantera:
                st.caption("Agregá una cantera desde el botón + de la lista o escribí una nueva.")

        col_camion, col_batea = st.columns(2)
        with col_camion:
            camion = render_campo_catalogo(
                "Camión",
                camiones,
                valor_actual=camion_default if not editando else remito.get("camion", ""),
                key_prefix=f"remito_camion_{identificador}",
            )
        with col_batea:
            batea = render_campo_catalogo(
                "Batea / Patente",
                bateas,
                valor_actual=batea_default if not editando else remito.get("batea", ""),
                key_prefix=f"remito_batea_{identificador}",
            )

        col_toneladas, col_material = st.columns(2)
        with col_toneladas:
            toneladas = st.number_input(
                "Toneladas",
                min_value=0.0,
                value=float(remito["toneladas"]) if editando else 0.0,
                step=0.001,
                format="%.3f",
            )
        with col_material:
            material = render_campo_catalogo(
                "Material",
                materiales,
                valor_actual=remito.get("material", "") if editando else "",
                key_prefix=f"remito_material_{identificador}",
            )
            if not material:
                st.caption("Agregá un material desde el botón + de la lista o escribí uno nuevo.")

        col_tarifa, col_subtotal = st.columns(2)
        with col_tarifa:
            tarifa = st.number_input(
                "Tarifa por tonelada",
                min_value=0.0,
                value=float(remito["tarifa"]) if editando else 0.0,
                step=0.01,
                format="%.2f",
            )
        with col_subtotal:
            subtotal = Decimal(str(toneladas)) * Decimal(str(tarifa))
            st.metric("Subtotal (sin IVA)", f"$ {subtotal:,.2f}")

        guardar = st.form_submit_button("Actualizar remito" if editando else "Guardar remito", type="primary")

    if not guardar:
        return
    if not numero_remito.strip().isdigit() or int(numero_remito) <= 0:
        st.warning("Ingresá un número de remito entero y mayor que cero.")
        return
    if not chofer.strip() or not cantera or not material.strip():
        st.warning("Completá el número, la fecha, el chofer, la cantera y el material.")
        return

    try:
        toneladas_decimal = decimal_positivo(toneladas, "Toneladas")
        tarifa_decimal = decimal_positivo(tarifa, "La tarifa")
        subtotal_decimal = (toneladas_decimal * tarifa_decimal).quantize(Decimal("0.01"))
        connection = obtener_conexion()
        try:
            with connection.cursor() as cursor:
                if editando:
                    cursor.execute(
                        """
                        UPDATE remitos_app
                        SET numero_remito = %s, fecha = %s, chofer = %s,
                            cantera = %s, camion = %s, batea = %s,
                            toneladas = %s, material = %s, tarifa = %s, subtotal = %s,
                            actualizado_en = NOW()
                        WHERE id = %s
                        """,
                        (
                            int(numero_remito),
                            fecha,
                            chofer.strip(),
                            cantera,
                            camion,
                            batea,
                            toneladas_decimal,
                            material.strip(),
                            tarifa_decimal,
                            subtotal_decimal,
                            int(remito["id"]),
                        ),
                    )
                    registrar_auditoria(
                        cursor,
                        "Editar remito",
                        f"Remito #{numero_remito}",
                        "Se actualizaron fecha, chofer, cantera, camión, batea, toneladas, material y tarifa.",
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO remitos_app
                            (numero_remito, fecha, chofer, cantera, camion, batea,
                             toneladas, material, tarifa, subtotal, creado_por)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            int(numero_remito),
                            fecha,
                            chofer.strip(),
                            cantera,
                            camion,
                            batea,
                            toneladas_decimal,
                            material.strip(),
                            tarifa_decimal,
                            subtotal_decimal,
                            st.session_state.usuario_actual,
                        ),
                    )
                    registrar_auditoria(
                        cursor,
                        "Crear remito",
                        f"Remito #{numero_remito}",
                        f"Se cargó el remito con cantera {cantera}, camión {camion or 'sin asignar'} y batea {batea or 'sin asignar'}.",
                    )
            connection.commit()
        finally:
            connection.close()
        st.success("Remito guardado correctamente.")
        st.rerun()
    except ValueError as error:
        st.warning(str(error))
    except Exception as error:
        mostrar_error("guardar el remito", error)


def mostrar_login():
    st.subheader("🔒 Sistema de Gestión Logística y Minería")
    tab_login, tab_registro = st.tabs(["🔑 Iniciar Sesión", "📝 Registrarse"])

    with tab_login, st.form("form_login"):
        user = st.text_input("Usuario", key="login_user")
        password = st.text_input("Contraseña", type="password", key="login_pass")
        if st.form_submit_button("Ingresar", type="primary"):
            try:
                connection = obtener_conexion()
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT rol, estado, password FROM usuarios
                        WHERE nombre_usuario = %s
                        """,
                        (user,),
                    )
                    result = cursor.fetchone()
                connection.close()
                password_ok, legacy_password = (
                    verificar_password(password, result[2]) if result else (False, False)
                )
                if password_ok and result[1] == "Aprobado":
                    if legacy_password:
                        connection = obtener_conexion()
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "UPDATE usuarios SET password = %s WHERE nombre_usuario = %s",
                                (generar_hash_password(password), user),
                            )
                        connection.commit()
                        connection.close()
                    st.session_state.usuario_actual = user
                    st.session_state.rol_usuario = result[0]
                    st.rerun()
                elif password_ok and result[1] == "Pendiente":
                    st.warning("Tu cuenta está pendiente de aprobación.")
                elif result:
                    st.error("Tu acceso fue rechazado o deshabilitado.")
                else:
                    st.error("Usuario o contraseña incorrectos.")
            except psycopg2.Error as error:
                st.error("PostgreSQL rechazó la conexión.")
                registrar_error("iniciar sesión", error)
                st.caption(
                    "Revisá host, base, usuario, contraseña y puerto en Secrets."
                )
                st.code(str(error).splitlines()[0])
            except Exception as error:  # noqa: BLE001
                st.error("No se pudo iniciar sesión.")
                registrar_error("iniciar sesión", error)
                st.caption(f"Detalle técnico: {error}")

    with tab_registro, st.form("form_registro"):
        nuevo_user = st.text_input("Elegí un nombre de usuario", key="reg_user")
        nueva_pass = st.text_input(
            "Elegí una contraseña", type="password", key="reg_pass"
        )
        if st.form_submit_button("Solicitar acceso"):
            if not nuevo_user.strip() or not nueva_pass:
                st.warning("Completá todos los campos.")
            else:
                try:
                    connection = obtener_conexion()
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT 1 FROM usuarios WHERE nombre_usuario = %s",
                            (nuevo_user.strip(),),
                        )
                        if cursor.fetchone():
                            st.error("El usuario ya existe.")
                        else:
                            cursor.execute(
                                """
                                INSERT INTO usuarios
                                    (nombre_usuario, password, rol, estado)
                                VALUES (%s, %s, 'Operador', 'Pendiente')
                                """,
                                (nuevo_user.strip(), generar_hash_password(nueva_pass)),
                            )
                            connection.commit()
                            st.success(
                                "Registro completado. Falta la aprobación del administrador."
                            )
                    connection.close()
                except Exception:  # noqa: BLE001
                    mostrar_error("registrar el usuario")


st.set_page_config(
    page_title="Gestión Logística y Minería",
    page_icon="🚛",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root { --ink: #f4f7fb; --muted: #9aa7b8; --line: #263244; --accent: #ff5a52; --panel: #111b28; --soft: #1a2635; }
    * { transition: none !important; animation: none !important; animation-duration: 0s !important; animation-delay: 0s !important; }
    .stApp { background: #0b1118; color: var(--ink); }
    .block-container { max-width: 1320px; padding-top: 1.2rem; padding-bottom: 2rem; }
    h1 { letter-spacing: -0.03em; font-weight: 800; }
    h2, h3 { letter-spacing: -0.02em; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMetric"] { background: #131c27; border: 1px solid var(--line); padding: 0.85rem 1rem; border-radius: 12px; }
    [data-testid="stForm"] { background: rgba(17, 27, 40, 0.92); border: 1px solid var(--line); border-radius: 12px; padding: 0.9rem; }
    [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
    [data-testid="stContainer"] { gap: 0.25rem; }
    .stButton > button { border-radius: 10px; font-weight: 650; padding: 0.45rem 0.8rem; }
    .stButton > button[kind="primary"] { background: var(--accent); border-color: var(--accent); }
    div[data-baseweb="tab-list"] { gap: 0.35rem; border-bottom: 1px solid var(--line); }
    button[data-baseweb="tab"] { color: var(--muted); padding: 0.55rem 0.8rem; border-radius: 10px 10px 0 0; }
    button[data-baseweb="tab"][aria-selected="true"] { color: var(--ink); background: rgba(255,255,255,0.02); }
    .stSelectbox > div, .stTextInput > div, .stDateInput > div, .stNumberInput > div, .stMultiselect > div { border-radius: 10px; }
    .stForm { gap: 0.15rem; }
    .stForm > div { gap: 0.15rem; }
    .stDataFrame .stDataFrameContainer { padding: 0; }
    .stExpander { border-radius: 10px; border: 1px solid var(--line); }
    .stTabs [data-testid="stVerticalBlockBorderWrapper"] { gap: 0.15rem; }
    .stMarkdown p, .stMarkdown li { margin: 0.15rem 0; }
    .css-1d391kg, .css-18ni7ap { padding-top: 0.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "usuario_actual" not in st.session_state:
    st.session_state.usuario_actual = None
    st.session_state.rol_usuario = None

try:
    connection = obtener_conexion()
    preparar_tabla_usuarios(connection)
    connection.close()
except Exception as error:
    st.error("No se pudo preparar la tabla de usuarios.")
    registrar_error("preparar tabla de usuarios", error)
    st.caption(f"Detalle técnico: {error}")
    st.stop()

try:
    connection = obtener_conexion()
    preparar_tabla_remitos(connection)
    preparar_tablas_flota(connection)
    connection.close()
except Exception as error:
    st.error("No se pudo preparar la base de datos para remitos y flota.")
    st.caption("Verificá que el usuario de Supabase pueda crear las tablas.")
    registrar_error("preparar tablas de la aplicación", error)
    st.code(str(error).splitlines()[0])
    st.stop()

if st.session_state.usuario_actual is None:
    mostrar_login()
    st.stop()

header_col, help_col, audit_col, logout_col = st.columns([6, 1, 1.5, 1.5])
with header_col:
    st.title("🚛 Sistema de gestión Logística y Minería")
    st.write(
        f"👤 Usuario: **{st.session_state.usuario_actual}** | "
        f"Rol: {st.session_state.rol_usuario}"
    )
with help_col:
    if st.button("?", help="Ver el reporte de errores de esta sesión"):
        st.session_state.mostrar_errores = not st.session_state.get(
            "mostrar_errores", False
        )
with audit_col:
    if st.button("🕘", help="Ver quién modificó cada documento y cuándo"):
        st.session_state.mostrar_auditoria = not st.session_state.get(
            "mostrar_auditoria", False
        )
with logout_col:
    if st.button("🚪 Cerrar sesión"):
        cerrar_sesion()

if st.session_state.get("mostrar_errores", False):
    with st.container(border=True):
        mostrar_panel_errores()

if st.session_state.get("mostrar_auditoria", False):
    with st.container(border=True):
        mostrar_panel_auditoria()

pestanas = ["📥 Remitos", "🚛 Flota", "📊 Reportes", "🧾 Facturación", "📷 Escáner IA"]
es_admin_supremo = st.session_state.usuario_actual == ADMIN_USER
if es_admin_supremo:
    pestanas.append("👥 Aprobar Usuarios")
tabs = st.tabs(pestanas)

with tabs[0]:
    st.header("Remitos")
    st.caption("Carga manual y consulta de todos los remitos guardados.")
    try:
        remitos = cargar_remitos()
    except Exception as error:
        st.error("No se pudo cargar los remitos.")
        st.caption("La tabla puede tener una estructura anterior o faltar permisos.")
        registrar_error("cargar remitos", error)
        st.code(str(error).splitlines()[0])
        remitos = pd.DataFrame()

    if not remitos.empty:
        col_buscar, col_btn = st.columns([3, 1])
        with col_buscar:
            numero_buscar = st.text_input("Número de remito a editar", key="remito_buscar_numero", placeholder="Ej: 12345")
        with col_btn:
            st.write("")
            if st.button("Buscar", type="primary", use_container_width=True):
                valor = str(numero_buscar).strip()
                if valor.isdigit():
                    remito_encontrado = remitos.loc[remitos["numero_remito"].astype(str) == valor]
                    if not remito_encontrado.empty:
                        st.session_state.remito_seleccionado_id = int(remito_encontrado.iloc[0]["id"])
                    else:
                        st.session_state.remito_seleccionado_id = None
                        st.warning("No existe un remito con ese número.")
                else:
                    st.session_state.remito_seleccionado_id = None
                    st.warning("Ingresá un número válido para buscar un remito.")
        seleccionado_id = st.session_state.get("remito_seleccionado_id")
        if seleccionado_id is not None:
            seleccionado = remitos.loc[remitos["id"] == int(seleccionado_id)].iloc[0]
        else:
            seleccionado = None
    else:
        seleccionado = None
    formulario_remito(seleccionado)

    st.divider()
    st.subheader("Lista de remitos cargados")
    if remitos.empty:
        st.info("Todavía no hay remitos cargados.")
    else:
        remitos_filtrados = aplicar_filtros_remitos(
            remitos,
            {
                "anio": st.session_state.get("filtro_remito_anio", "Todos"),
                "mes": st.session_state.get("filtro_remito_mes", "Todos"),
                "cantera": st.session_state.get("filtro_remito_cantera", "Todos"),
                "material": st.session_state.get("filtro_remito_material", "Todos"),
                "chofer": st.session_state.get("filtro_remito_chofer", "Todos"),
                "patente": st.session_state.get("filtro_remito_patente", "Todos"),
                "camion": st.session_state.get("filtro_remito_camion", "Todos"),
            },
        )
        filtros_col_1, filtros_col_2, filtros_col_3, filtros_col_4, filtros_col_5, filtros_col_6, filtros_col_7, filtros_col_8 = st.columns([1.3, 1.3, 1.6, 1.6, 1.8, 1.6, 1.6, 0.5])
        with filtros_col_1:
            anios = ["Todos"] + sorted(remitos["fecha"].dt.year.dropna().astype(int).unique().tolist(), reverse=True)
            st.session_state.filtro_remito_anio = st.selectbox("Año", anios, index=0, key="filtro_remito_anio")
        with filtros_col_2:
            meses = ["Todos"] + list(pd.date_range("2000-01-01", periods=12, freq="MS").strftime("%B"))
            st.session_state.filtro_remito_mes = st.selectbox("Mes", meses, index=0, key="filtro_remito_mes")
        with filtros_col_3:
            st.session_state.filtro_remito_cantera = st.selectbox(
                "Cantera",
                construir_opciones_filtro(remitos["cantera"]),
                index=0,
                key="filtro_remito_cantera",
            )
        with filtros_col_4:
            st.session_state.filtro_remito_material = st.selectbox(
                "Material",
                construir_opciones_filtro(remitos["material"]),
                index=0,
                key="filtro_remito_material",
            )
        with filtros_col_5:
            st.session_state.filtro_remito_chofer = st.selectbox(
                "Chofer",
                construir_opciones_filtro(remitos["chofer"]),
                index=0,
                key="filtro_remito_chofer",
            )
        with filtros_col_6:
            st.session_state.filtro_remito_patente = st.selectbox(
                "Patente",
                construir_opciones_filtro(remitos["batea"]),
                index=0,
                key="filtro_remito_patente",
            )
        with filtros_col_7:
            st.session_state.filtro_remito_camion = st.selectbox(
                "Camión",
                construir_opciones_filtro(remitos["camion"]),
                index=0,
                key="filtro_remito_camion",
            )
        with filtros_col_8, st.popover("+", use_container_width=True):
            tipo_catalogo = st.selectbox("Agregar", ["Cantera", "Material"], key="catalogo_remito_tipo")
            nuevo_catalogo = st.text_input("Nombre", key="catalogo_remito_nombre")
            if st.button("Guardar", key="guardar_catalogo_remito", type="primary"):
                valor = (nuevo_catalogo or "").strip()
                if not valor:
                    st.warning("Ingresá un nombre.")
                else:
                    try:
                        if tipo_catalogo == "Cantera":
                            guardar_cantera(valor)
                        else:
                            guardar_material(valor)
                        st.success(f"Se guardó {tipo_catalogo.lower()}.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as error:  # noqa: BLE001
                        mostrar_error(f"guardar la {tipo_catalogo.lower()}", error)

        vista = remitos_filtrados.rename(
            columns={
                "id": "ID",
                "numero_remito": "N° Remito",
                "fecha": "Fecha",
                "chofer": "Chofer",
                "cantera": "Cantera",
                "camion": "Camión",
                "batea": "Batea",
                "toneladas": "Toneladas",
                "material": "Material",
                "tarifa": "Tarifa",
                "subtotal": "Subtotal",
                "creado_por": "Cargado por",
            }
        )
        if remitos_filtrados.empty:
            st.info("No hay remitos para los filtros seleccionados.")
        else:
            st.dataframe(
                vista[
                    [
                        "N° Remito",
                        "Fecha",
                        "Chofer",
                        "Cantera",
                        "Camión",
                        "Batea",
                        "Toneladas",
                        "Material",
                        "Tarifa",
                        "Subtotal",
                        "Cargado por",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

with tabs[1]:
    titulo_col, buscar_col, boton_col = st.columns([7, 1.5, 1.5])
    with titulo_col:
        st.header("Flota")
    with buscar_col:
        if st.button("🔍", help="Buscar y editar un recurso"):
            st.session_state.mostrar_busqueda_flota = not st.session_state.get(
                "mostrar_busqueda_flota", False
            )
            st.session_state.vista_flota = "Recursos separados"
    with boton_col:
        if st.button("+ Agregar", type="primary"):
            st.session_state.mostrar_formulario_flota = True

    if st.session_state.get("mostrar_formulario_flota", False):
        tipo_recurso = st.selectbox(
            "¿Qué recurso querés agregar?",
            ["Chofer", "Batea", "Camión"],
            key="tipo_recurso_flota",
        )
        mostrar_formulario_flota(tipo_recurso)

    vista_flota = st.radio(
        "Vista",
        ["Cuadro de asignaciones", "Recursos separados"],
        horizontal=True,
        key="vista_flota",
    )
    if vista_flota == "Cuadro de asignaciones":
        mostrar_cuadro_asignaciones()
    else:
        st.subheader("Choferes")
        editar_recursos_flota(
            "choferes",
            [
                "nombre_completo", "nombre", "apellido", "dni", "nro_licencia",
            "estado", "vencimiento_licencia", "preocupacional",
                "curso_manejo",
            ],
            "choferes",
        )
        st.subheader("Bateas")
        editar_recursos_flota(
            "bateas",
            ["patente", "capacidad", "tipo", "marca", "vencimiento_seguro", "modelo", "service"],
            "bateas",
        )
        st.subheader("Camiones")
        editar_recursos_flota(
            "camiones",
            [
                "itv", "service", "patente", "marca", "estado", "kilometraje",
                "control_periodico", "seguro",
            ],
            "camiones",
        )

    if st.session_state.get("mostrar_busqueda_flota", False):
        st.divider()
        st.subheader("Buscar y editar recurso")
        st.caption("La búsqueda y los editores aparecen en la vista Recursos separados.")

with tabs[2]:
    st.header("Reportes")
    st.info("Los reportes se construirán sobre la lista de remitos cargados.")

with tabs[3]:
    st.header("🧾 Facturación")
    st.caption("Agrupá los remitos por cantera y factura para saber qué factura agrupa cada servicio.")

    canteras = cargar_canteras()
    if canteras.empty:
        st.info("Todavía no hay canteras cargadas. Agregá una para empezar a facturar.")
    else:
        cantera_actual = st.session_state.get("cantera_facturacion", int(canteras.iloc[0]["id"]))
        nombre_cantera_actual = canteras.loc[canteras["id"] == int(cantera_actual), "nombre"].iloc[0]
        cantera_seleccionada = st.selectbox(
            "Cantera",
            canteras["nombre"].tolist(),
            index=canteras["nombre"].tolist().index(nombre_cantera_actual),
        )
        cantera_id = int(canteras.loc[canteras["nombre"] == cantera_seleccionada, "id"].iloc[0])
        st.session_state.cantera_facturacion = cantera_id

    with st.form("form_nueva_cantera"):
        nombre_nueva_cantera = st.text_input("Agregar cantera")
        if st.form_submit_button("Guardar cantera", type="primary") and nombre_nueva_cantera.strip():
            try:
                guardar_cantera(nombre_nueva_cantera)
                st.success("Cantera guardada.")
                st.rerun()
            except Exception as error:  # noqa: BLE001
                mostrar_error("guardar la cantera", error)

    if not canteras.empty:
        st.subheader("Facturas de la cantera")
        remitos_cantera = obtener_remitos_facturacion(cantera_seleccionada)
        facturas = obtener_facturas_por_cantera(cantera_id)

        with st.form("form_nueva_factura"):
            col_nom, col_fecha, col_guardar = st.columns([3, 2, 1.5])
            with col_nom:
                nombre_factura = st.text_input("Nombre de factura", key="nombre_factura")
            with col_fecha:
                fecha_factura = st.date_input("Fecha", value=fecha_hoy(), key="fecha_factura")
            with col_guardar:
                st.write("")
                st.write("")
                guardar_factura = st.form_submit_button("Agregar factura", type="primary")
            if not remitos_cantera.empty:
                remitos_seleccionados = st.multiselect(
                    "Remitos a incluir",
                    options=[f"#{int(fila['numero_remito'])} · {fila['fecha']} · {fila['material']} · ${float(fila['subtotal']):,.2f}" for _, fila in remitos_cantera.iterrows()],
                    default=[],
                    key="remitos_factura",
                )
            else:
                st.info("No hay remitos cargados para esta cantera todavía.")

        if guardar_factura:
            if nombre_factura.strip() and remitos_cantera.empty == False:
                ids = []
                for texto in remitos_seleccionados:
                    numero = texto.split("#", 1)[1].split(" · ", 1)[0]
                    remito = remitos_cantera.loc[remitos_cantera["numero_remito"].astype(str) == str(numero)].iloc[0]
                    ids.append(int(remito["id"]))
                try:
                    crear_factura(cantera_id, nombre_factura, fecha_factura, ids)
                    st.success("Factura creada.")
                    st.rerun()
                except Exception as error:
                    mostrar_error("crear la factura", error)
            else:
                st.warning("Asigná un nombre y al menos un remito para crear la factura.")

        if facturas.empty:
            st.info("Todavía no hay facturas creadas para esta cantera.")
        else:
            for _, factura in facturas.iterrows():
                with st.expander(f"{factura['nombre']} · {factura['fecha']} · {int(factura['remitos'])} remitos"):
                    st.write(f"Total estimado: ${float(factura['total']):,.2f}")
                    conexion = obtener_conexion()
                    try:
                        remitos_factura = pd.read_sql_query(
                            """
                            SELECT r.numero_remito, r.fecha, r.chofer, r.material, r.subtotal
                            FROM factura_remitos fr
                            JOIN remitos_app r ON r.id = fr.remito_id
                            WHERE fr.factura_id = %s
                            ORDER BY r.fecha DESC, r.id DESC
                            """,
                            conexion,
                            params=(int(factura["id"]),),
                        )
                    finally:
                        conexion.close()
                    if remitos_factura.empty:
                        st.caption("Sin remitos asociados.")
                    else:
                        st.dataframe(remitos_factura.rename(columns={
                            "numero_remito": "N° Remito",
                            "fecha": "Fecha",
                            "chofer": "Chofer",
                            "material": "Material",
                            "subtotal": "Subtotal",
                        }), use_container_width=True, hide_index=True)

with tabs[4]:
    st.header("Escáner IA")
    st.info("Aquí se incorporará la lectura de remitos mediante fotografía.")

if es_admin_supremo:
    with tabs[5]:
        st.header("👥 Solicitudes pendientes")
        try:
            connection = obtener_conexion()
            pendientes = pd.read_sql_query(
                """
                SELECT nombre_usuario FROM usuarios
                WHERE estado = 'Pendiente' ORDER BY nombre_usuario
                """,
                connection,
            )
            connection.close()
            if pendientes.empty:
                st.info("No hay nadie esperando aprobación.")
            else:
                for usuario in pendientes["nombre_usuario"]:
                    col_user, col_action = st.columns([6, 2])
                    col_user.write(f"👤 **{usuario}** quiere entrar.")
                    if col_action.button("✅ Aprobar", key=f"aprobar_{usuario}"):
                        connection = obtener_conexion()
                        with connection.cursor() as cursor:
                            cursor.execute(
                                """
                                UPDATE usuarios SET estado = 'Aprobado'
                                WHERE nombre_usuario = %s
                                """,
                                (usuario,),
                            )
                        connection.commit()
                        connection.close()
                        st.rerun()
        except Exception as error:
            mostrar_error("cargar las solicitudes", error)
