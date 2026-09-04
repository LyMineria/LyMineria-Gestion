# -*- coding: utf-8 -*-
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import secrets

import pandas as pd
import psycopg2
import streamlit as st

ADMIN_USER = "OcampoElio"


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
    """Crea o completa la tabla compartida por carga manual y futuro lector IA."""
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
        cursor.execute(
            """
            ALTER TABLE remitos
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS fecha DATE,
                ADD COLUMN IF NOT EXISTS chofer VARCHAR(150),
                ADD COLUMN IF NOT EXISTS toneladas NUMERIC(12, 3),
                ADD COLUMN IF NOT EXISTS material VARCHAR(200),
                ADD COLUMN IF NOT EXISTS tarifa NUMERIC(14, 2),
                ADD COLUMN IF NOT EXISTS subtotal NUMERIC(16, 2),
                ADD COLUMN IF NOT EXISTS creado_por VARCHAR(150),
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW(),
                ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ DEFAULT NOW()
            """
        )
    connection.commit()


def preparar_tabla_usuarios(connection):
    """Deja espacio suficiente para hashes de contraseñas."""
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE usuarios ALTER COLUMN password TYPE VARCHAR(255)"
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
                curso_manejo VARCHAR(150),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS bateas (
                id BIGSERIAL PRIMARY KEY,
                patente VARCHAR(20) NOT NULL,
                capacidad NUMERIC(12, 3) NOT NULL CHECK (capacidad >= 0),
                tipo VARCHAR(100) NOT NULL,
                marca VARCHAR(100) NOT NULL,
                seguro VARCHAR(150),
                modelo INTEGER,
                service DATE,
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
                seguro VARCHAR(150),
                creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                ADD COLUMN IF NOT EXISTS curso_manejo VARCHAR(150),
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            UPDATE choferes
            SET nombre_completo = NULLIF(TRIM(COALESCE(nombre, '') || ' ' || COALESCE(apellido, '')), '')
            WHERE nombre_completo IS NULL;
            ALTER TABLE choferes ALTER COLUMN nombre_completo DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN dni DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN nro_licencia DROP NOT NULL;
            ALTER TABLE choferes ALTER COLUMN estado DROP NOT NULL;
            ALTER TABLE bateas
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS patente VARCHAR(20),
                ADD COLUMN IF NOT EXISTS capacidad NUMERIC(12, 3),
                ADD COLUMN IF NOT EXISTS tipo VARCHAR(100),
                ADD COLUMN IF NOT EXISTS marca VARCHAR(100),
                ADD COLUMN IF NOT EXISTS seguro VARCHAR(150),
                ADD COLUMN IF NOT EXISTS modelo INTEGER,
                ADD COLUMN IF NOT EXISTS service DATE,
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            ALTER TABLE camiones
                ADD COLUMN IF NOT EXISTS id BIGSERIAL,
                ADD COLUMN IF NOT EXISTS itv DATE,
                ADD COLUMN IF NOT EXISTS service DATE,
                ADD COLUMN IF NOT EXISTS patente VARCHAR(20),
                ADD COLUMN IF NOT EXISTS marca VARCHAR(100),
                ADD COLUMN IF NOT EXISTS estado VARCHAR(20),
                ADD COLUMN IF NOT EXISTS kilometraje NUMERIC(12, 2),
                ADD COLUMN IF NOT EXISTS control_periodico DATE,
                ADD COLUMN IF NOT EXISTS seguro VARCHAR(150),
                ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT NOW();
            CREATE TABLE IF NOT EXISTS asignaciones_flota (
                id BIGSERIAL PRIMARY KEY,
                camion_id BIGINT REFERENCES camiones(id) ON DELETE SET NULL,
                batea_id BIGINT REFERENCES bateas(id) ON DELETE SET NULL,
                chofer_id BIGINT REFERENCES choferes(id) ON DELETE SET NULL,
                destino VARCHAR(200),
                actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    st.caption("Revisá los Secrets de Streamlit y la disponibilidad de Supabase.")


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
    st.caption("Elegí qué camion, batea y chofer trabajan juntos y definí el destino.")
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
    camion_opciones.update(
        {
            f"Camión {fila.id} | {fila.patente} | {fila.marca}": int(fila.id)
            for fila in camiones.itertuples()
        }
    )
    batea_opciones = {"Sin asignar": None}
    batea_opciones.update(
        {
            f"Batea {fila.id} | {fila.patente} | {fila.marca}": int(fila.id)
            for fila in bateas.itertuples()
        }
    )
    chofer_opciones = {"Sin asignar": None}
    chofer_opciones.update(
        {
            f"Chofer {fila.id} | {fila.nombre} {fila.apellido}": int(fila.id)
            for fila in choferes.itertuples()
        }
    )
    cantidad_filas = max(len(camiones), len(bateas), len(choferes), 1)
    with st.form("form_asignaciones"):
        asignaciones = []
        for indice in range(cantidad_filas):
            st.markdown(f"**Unidad {indice + 1}**")
            col_camion, col_batea, col_chofer, col_destino = st.columns(4)
            with col_camion:
                camion_guardado = (
                    asignaciones_guardadas.iloc[indice]["camion_id"]
                    if indice < len(asignaciones_guardadas)
                    else None
                )
                camion_index = next(
                    (pos for pos, valor in enumerate(camion_opciones.values())
                     if valor == camion_guardado),
                    0,
                )
                camion = st.selectbox(
                    "Camión", list(camion_opciones), index=camion_index,
                    key=f"asig_camion_{indice}"
                )
            with col_batea:
                batea_guardada = (
                    asignaciones_guardadas.iloc[indice]["batea_id"]
                    if indice < len(asignaciones_guardadas)
                    else None
                )
                batea_index = next(
                    (pos for pos, valor in enumerate(batea_opciones.values())
                     if valor == batea_guardada),
                    0,
                )
                batea = st.selectbox(
                    "Batea", list(batea_opciones), index=batea_index,
                    key=f"asig_batea_{indice}"
                )
            with col_chofer:
                chofer_guardado = (
                    asignaciones_guardadas.iloc[indice]["chofer_id"]
                    if indice < len(asignaciones_guardadas)
                    else None
                )
                chofer_index = next(
                    (pos for pos, valor in enumerate(chofer_opciones.values())
                     if valor == chofer_guardado),
                    0,
                )
                chofer = st.selectbox(
                    "Chofer", list(chofer_opciones), index=chofer_index,
                    key=f"asig_chofer_{indice}"
                )
            with col_destino:
                destino_guardado = (
                    asignaciones_guardadas.iloc[indice]["destino"]
                    if indice < len(asignaciones_guardadas)
                    else ""
                )
                destino = st.text_input(
                    "Destino", value=destino_guardado or "",
                    key=f"asig_destino_{indice}"
                )
            asignaciones.append(
                (camion_opciones[camion], batea_opciones[batea],
                 chofer_opciones[chofer], destino.strip())
            )
        guardar = st.form_submit_button("Guardar asignaciones", type="primary")

    if guardar:
        try:
            connection = obtener_conexion()
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM asignaciones_flota")
                for camion_id, batea_id, chofer_id, destino in asignaciones:
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
        if tipo == "Chofer":
            nombre = st.text_input("Nombre")
            apellido = st.text_input("Apellido")
            dni = st.text_input("DNI")
            licencia = st.text_input("Nro. licencia")
            estado = st.selectbox("Estado", ["Activo", "Vacaciones", "Licencia"])
            vencimiento = st.date_input("Vencimiento licencia", value=date.today())
            preocupacional = st.date_input("Preocupacional", value=date.today())
            cuil = st.text_input("CUIL")
            curso = st.date_input("Curso de manejo", value=date.today())
        elif tipo == "Batea":
            patente = st.text_input("Patente")
            capacidad = st.number_input("Capacidad (toneladas)", min_value=0.0, step=0.001)
            tipo_batea = st.text_input("Tipo")
            marca = st.text_input("Marca")
            seguro = st.text_input("Seguro")
            modelo = st.number_input("Modelo (año)", min_value=1900, max_value=2100, value=2026)
            service = st.date_input("Service", value=date.today())
        else:
            itv = st.date_input("ITV", value=date.today())
            service = st.date_input("Service", value=date.today())
            patente = st.text_input("Patente")
            marca = st.text_input("Marca")
            estado = st.selectbox("Estado", ["Roto", "Funcional", "Pausa"])
            kilometraje = st.number_input("Kilometraje", min_value=0.0, step=1.0)
            control = st.date_input("Control periódico", value=date.today())
            seguro = st.text_input("Seguro")

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
                     vencimiento_licencia, preocupacional, cuil, curso_manejo)
                        VALUES (%s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s,
                            %s, %s, NULLIF(%s, ''), %s)
                    """,
                        (f"{nombre.strip()} {apellido.strip()}", nombre.strip(),
                         apellido.strip(), dni.strip(), licencia.strip(), estado,
                         vencimiento, preocupacional, cuil.strip(), curso.isoformat()),
                )
            elif tipo == "Batea":
                if not all([patente.strip(), tipo_batea.strip(), marca.strip()]):
                    st.warning("Completá patente, tipo y marca.")
                    connection.close()
                    return
                cursor.execute(
                    """
                    INSERT INTO bateas
                    (patente, capacidad, tipo, marca, seguro, modelo, service)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (patente.strip(), capacidad, tipo_batea.strip(), marca.strip(),
                     seguro.strip(), modelo, service),
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
                     kilometraje, control, seguro.strip()),
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
    st.subheader("Editar remito" if editando else "Cargar remito manual")

    with st.form(f"form_remito_{identificador}"):
        try:
            choferes = cargar_choferes_activos()
        except Exception as error:
            st.error("No se pudieron cargar los choferes activos.")
            registrar_error("cargar choferes activos", error)
            st.code(str(error).splitlines()[0])
            return
        if choferes.empty:
            st.warning("Primero cargá un chofer con estado Activo en Flota.")
            return
        choferes["etiqueta"] = choferes.apply(
            lambda row: f"{row['nombre']} {row['apellido']} (ID {row['id']})",
            axis=1,
        )
        opciones_chofer = choferes["etiqueta"].tolist()
        chofer_actual = str(remito["chofer"]) if editando else ""
        indice_chofer = next(
            (
                index
                for index, etiqueta in enumerate(opciones_chofer)
                if etiqueta.startswith(chofer_actual + " ")
            ),
            0,
        )
        chofer = st.selectbox("Chofer", opciones_chofer, index=indice_chofer)
        fecha = st.date_input(
            "Fecha",
            value=pd.to_datetime(remito["fecha"]).date()
            if editando
            else date.today(),
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
    except Exception as error:
        mostrar_error("guardar el remito", error)


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
                registrar_error("iniciar sesión", error)
                st.caption(
                    "Revisá host, base, usuario, contraseña y puerto en Secrets."
                )
                st.code(str(error).splitlines()[0])
            except Exception as error:
                st.error("No se pudo iniciar sesión.")
                registrar_error("iniciar sesión", error)
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

try:
    connection = obtener_conexion()
    preparar_tabla_usuarios(connection)
    connection.close()
except Exception as error:
    st.error("No se pudo preparar la tabla de usuarios.")
    registrar_error("preparar tabla de usuarios", error)
    st.caption(f"Detalle técnico: {error}")
    st.stop()

if st.session_state.usuario_actual is None:
    mostrar_login()
    st.stop()

header_col, help_col, logout_col = st.columns([7, 1, 2])
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
with logout_col:
    if st.button("🚪 Cerrar sesión"):
        cerrar_sesion()

if st.session_state.get("mostrar_errores", False):
    with st.container(border=True):
        mostrar_panel_errores()

try:
    connection = obtener_conexion()
    preparar_tabla_remitos(connection)
    preparar_tablas_flota(connection)
    connection.close()
except Exception as error:
    st.error("No se pudo preparar la base de datos para remitos.")
    st.caption("Verificá que el usuario de Supabase pueda crear las tablas.")
    registrar_error("preparar tablas de la aplicación", error)
    st.code(str(error).splitlines()[0])
    st.stop()

pestanas = ["📥 Remitos", "🚛 Flota", "📊 Reportes", "📷 Escáner IA"]
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
                "estado", "vencimiento_licencia", "preocupacional", "cuil",
                "curso_manejo",
            ],
            "choferes",
        )
        st.subheader("Bateas")
        editar_recursos_flota(
            "bateas",
            ["patente", "capacidad", "tipo", "marca", "seguro", "modelo", "service"],
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
    st.header("Escáner IA")
    st.info("Aquí se incorporará la lectura de remitos mediante fotografía.")

if es_admin_supremo:
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
        except Exception as error:
            mostrar_error("cargar las solicitudes", error)
