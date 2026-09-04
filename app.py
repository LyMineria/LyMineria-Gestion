# -*- coding: utf-8 -*-
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import secrets

import pandas as pd
import psycopg2
import streamlit as st


def obtener_conexion():
    """Abre PostgreSQL usando únicamente secrets de Streamlit."""
    config = st.secrets["database"] if "database" in st.secrets else st.secrets
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
    """Usa una tabla compartida por la carga manual y el futuro lector IA."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS remitos (
                id BIGSERIAL PRIMARY KEY,
                fecha DATE NOT NULL,
                chofer VARCHAR(150) NOT NULL,
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
    connection.commit()


def cerrar_sesion():
    st.session_state.pop("usuario_actual", None)
    st.session_state.pop("rol_usuario", None)
    st.rerun()


def mostrar_error(accion):
    st.error(f"No se pudo {accion}.")
    st.caption("Revisá los Secrets de Streamlit y la disponibilidad de Supabase.")


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


def cargar_remitos():
    connection = obtener_conexion()
    try:
        return pd.read_sql_query(
            """
            SELECT id, fecha, chofer, toneladas, material, tarifa, subtotal,
                   creado_por, creado_en
            FROM remitos
            ORDER BY fecha DESC, id DESC
            """,
            connection,
        )
    finally:
        connection.close()


def formulario_remito(remito=None):
    editando = remito is not None
    identificador = str(remito["id"]) if editando else "nuevo"
    st.subheader("Editar remito" if editando else "Cargar remito manual")

    with st.form(f"form_remito_{identificador}"):
        fecha = st.date_input(
            "Fecha",
            value=pd.to_datetime(remito["fecha"]).date()
            if editando
            else date.today(),
        )
        chofer = st.text_input(
            "Chofer", value=str(remito["chofer"]) if editando else ""
        )
        toneladas = st.number_input(
            "Toneladas",
            min_value=0.0,
            value=float(remito["toneladas"]) if editando else 0.0,
            step=0.001,
            format="%.3f",
        )
        material = st.text_input(
            "Material", value=str(remito["material"]) if editando else ""
        )
        tarifa = st.number_input(
            "Tarifa por tonelada",
            min_value=0.0,
            value=float(remito["tarifa"]) if editando else 0.0,
            step=0.01,
            format="%.2f",
        )
        subtotal = Decimal(str(toneladas)) * Decimal(str(tarifa))
        st.metric("Subtotal (sin IVA)", f"$ {subtotal:,.2f}")
        guardar = st.form_submit_button(
            "Actualizar remito" if editando else "Guardar remito",
            type="primary",
        )

    if not guardar:
        return
    if not chofer.strip() or not material.strip():
        st.warning("Completá la fecha, el chofer y el material.")
        return

    try:
        toneladas_decimal = decimal_positivo(toneladas, "Toneladas")
        tarifa_decimal = decimal_positivo(tarifa, "La tarifa")
        subtotal_decimal = (toneladas_decimal * tarifa_decimal).quantize(
            Decimal("0.01")
        )
        connection = obtener_conexion()
        try:
            with connection.cursor() as cursor:
                if editando:
                    cursor.execute(
                        """
                        UPDATE remitos
                        SET fecha = %s, chofer = %s, toneladas = %s,
                            material = %s, tarifa = %s, subtotal = %s,
                            actualizado_en = NOW()
                        WHERE id = %s
                        """,
                        (
                            fecha,
                            chofer.strip(),
                            toneladas_decimal,
                            material.strip(),
                            tarifa_decimal,
                            subtotal_decimal,
                            int(remito["id"]),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO remitos
                            (fecha, chofer, toneladas, material, tarifa,
                             subtotal, creado_por)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            fecha,
                            chofer.strip(),
                            toneladas_decimal,
                            material.strip(),
                            tarifa_decimal,
                            subtotal_decimal,
                            st.session_state.usuario_actual,
                        ),
                    )
            connection.commit()
        finally:
            connection.close()
        st.success("Remito guardado correctamente.")
        st.rerun()
    except ValueError as error:
        st.warning(str(error))
    except Exception:
        mostrar_error("guardar el remito")


def mostrar_login():
    st.subheader("🔒 Sistema de Gestión Logística y Minería")
    tab_login, tab_registro = st.tabs(["🔑 Iniciar Sesión", "📝 Registrarse"])

    with tab_login:
        user = st.text_input("Usuario", key="login_user")
        password = st.text_input("Contraseña", type="password", key="login_pass")
        if st.button("Ingresar", type="primary"):
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
                st.caption(
                    "Revisá host, base, usuario, contraseña y puerto en Secrets."
                )
                st.code(str(error).splitlines()[0])
            except Exception as error:
                st.error("No se pudo iniciar sesión.")
                st.caption(f"Detalle técnico: {error}")

    with tab_registro:
        nuevo_user = st.text_input("Elegí un nombre de usuario", key="reg_user")
        nueva_pass = st.text_input(
            "Elegí una contraseña", type="password", key="reg_pass"
        )
        if st.button("Solicitar acceso"):
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
                except Exception:
                    mostrar_error("registrar el usuario")


st.set_page_config(page_title="Gestión Logística y Minería", layout="wide")

if "usuario_actual" not in st.session_state:
    st.session_state.usuario_actual = None
    st.session_state.rol_usuario = None

if st.session_state.usuario_actual is None:
    mostrar_login()
    st.stop()

header_col, logout_col = st.columns([8, 2])
with header_col:
    st.title("🚛 Sistema de gestión Logística y Minería")
    st.write(
        f"👤 Usuario: **{st.session_state.usuario_actual}** | "
        f"Rol: {st.session_state.rol_usuario}"
    )
with logout_col:
    if st.button("🚪 Cerrar sesión"):
        cerrar_sesion()

try:
    connection = obtener_conexion()
    preparar_tabla_remitos(connection)
    connection.close()
except Exception:
    st.error("No se pudo preparar la base de datos para remitos.")
    st.caption("Verificá que el usuario de Supabase pueda crear la tabla remitos.")
    st.stop()

pestanas = ["📥 Remitos", "🚛 Flota", "📊 Reportes", "📷 Escáner IA"]
if st.session_state.rol_usuario == "Admin":
    pestanas.append("👥 Aprobar Usuarios")
tabs = st.tabs(pestanas)

with tabs[0]:
    st.header("Remitos")
    st.caption("Carga manual y consulta de todos los remitos guardados.")
    try:
        remitos = cargar_remitos()
    except Exception:
        mostrar_error("cargar los remitos")
        remitos = pd.DataFrame()

    opciones = {"Nuevo remito": None}
    if not remitos.empty:
        opciones.update(
            {
                f"#{row.id} | {row.fecha} | {row.chofer} | {row.material}": row.id
                for row in remitos.itertuples()
            }
        )
    seleccion = st.selectbox("Seleccionar remito para editar", list(opciones))
    seleccionado_id = opciones[seleccion]
    seleccionado = (
        remitos.loc[remitos["id"] == seleccionado_id].iloc[0]
        if seleccionado_id is not None
        else None
    )
    formulario_remito(seleccionado)

    st.divider()
    st.subheader("Lista de remitos cargados")
    if remitos.empty:
        st.info("Todavía no hay remitos cargados.")
    else:
        vista = remitos.rename(
            columns={
                "id": "ID",
                "fecha": "Fecha",
                "chofer": "Chofer",
                "toneladas": "Toneladas",
                "material": "Material",
                "tarifa": "Tarifa",
                "subtotal": "Subtotal",
                "creado_por": "Cargado por",
            }
        )
        st.dataframe(
            vista[
                [
                    "ID",
                    "Fecha",
                    "Chofer",
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
    st.header("Flota")
    st.info("Esta sección queda preparada para la gestión de vehículos.")

with tabs[2]:
    st.header("Reportes")
    st.info("Los reportes se construirán sobre la lista de remitos cargados.")

with tabs[3]:
    st.header("Escáner IA")
    st.info("Aquí se incorporará la lectura de remitos mediante fotografía.")

if st.session_state.rol_usuario == "Admin":
    with tabs[4]:
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
        except Exception:
            mostrar_error("cargar las solicitudes")
