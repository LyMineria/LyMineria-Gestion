# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import psycopg2

def obtener_conexion():
    conn = psycopg2.connect(
        host="kopkotyrjtyvgqewdujh", # <-- Pegalo acá
        database="postgres",
        user="postgres",
        password="Brianpeirano1996",
        port="5432"
    )
    conn.set_client_encoding('UTF8')
    return conn

def cerrar_sesion():
    st.session_state.usuario_actual = None
    st.session_state.rol_usuario = None
    st.rerun()

st.set_page_config(page_title="Gestión Logística y Minería", layout="wide")

# ------------------------------------------------------------------
# CONTROL DE ACCESO Y REGISTRO
# ------------------------------------------------------------------
if 'usuario_actual' not in st.session_state:
    st.session_state.usuario_actual = None
    st.session_state.rol_usuario = None

if st.session_state.usuario_actual is None:
    st.subheader("🔒 Sistema de Gestión Logística y Minería")
    
    tab_login, tab_registro = st.tabs(["🔑 Iniciar Sesión", "📝 Registrarse"])
    
    with tab_login:
        user = st.text_input("Usuario", key="login_user")
        password = st.text_input("Contraseña", type="password", key="login_pass")
        
        if st.button("Ingresar"):
            try:
                conn = obtener_conexion()
                cur = conn.cursor()
                cur.execute("SELECT rol, estado FROM usuarios WHERE nombre_usuario = %s AND password = %s", (user, password))
                res = cur.fetchone()
                conn.close()
                
                if res:
                    rol, estado = res[0], res[1]
                    if estado == 'Aprobado':
                        st.session_state.usuario_actual = user
                        st.session_state.rol_usuario = rol
                        st.success("¡Bienvenido!")
                        st.rerun()
                    elif estado == 'Pendiente':
                        st.warning("⏳ Tu cuenta está pendiente de aprobación por el Administrador.")
                    else:
                        st.error("🚫 Acceso rechazado.")
                else:
                    st.error("Usuario o contraseña incorrectos.")
            except Exception as e:
                st.error(f"Error de base de datos. Asegurate de hacer el PASO 2 en Supabase. Detalles: {e}")

    with tab_registro:
        nuevo_user = st.text_input("Elegí un Nombre de Usuario", key="reg_user")
        nueva_pass = st.text_input("Elegí una Contraseña", type="password", key="reg_pass")
        
        if st.button("Solicitar Acceso"):
            if not nuevo_user or not nueva_pass:
                st.warning("Completá todos los campos.")
            else:
                try:
                    conn = obtener_conexion()
                    cur = conn.cursor()
                    cur.execute("SELECT nombre_usuario FROM usuarios WHERE nombre_usuario = %s", (nuevo_user,))
                    if cur.fetchone():
                        st.error("El usuario ya existe.")
                    else:
                        # Se registra como Pendiente
                        cur.execute("INSERT INTO usuarios (nombre_usuario, password, rol, estado) VALUES (%s, %s, 'Operador', 'Pendiente')", (nuevo_user, nueva_pass))
                        conn.commit()
                        st.success("✅ Registro completado. Avisale al Admin que te apruebe.")
                    conn.close()
                except Exception as e:
                    st.error(f"Error al registrar: {e}")
                    
    st.stop() # Frena la app acá si no está logueado

# ------------------------------------------------------------------
# SISTEMA PRINCIPAL (Solo logueados)
# ------------------------------------------------------------------
header_col, logout_col = st.columns([8, 2])
with header_col:
    st.title("🚛 Sistema de gestión Logística y Minería")
    st.write(f"👤 Usuario: **{st.session_state.usuario_actual}** | Rol: {st.session_state.rol_usuario}")
with logout_col:
    if st.button("🚪 Cerrar sesión"):
        cerrar_sesion()

pestanas = ["📥 Remitos", "🚛 Flota", "📊 Reportes", "📷 Escáner IA"]
if st.session_state.rol_usuario == 'Admin':
    pestanas.append("👥 Aprobar Usuarios")

tabs = st.tabs(pestanas)

# Pestañas vacías para no hacer el código gigante ahora (luego les ponemos contenido)
with tabs[0]: st.write("Sección de carga manual de remitos.")
with tabs[1]: st.write("Sección de gestión de flota.")
with tabs[2]: st.write("Sección de reportes.")
with tabs[3]: st.write("Sección de escáner.")

# Pestaña exclusiva de Administrador para aprobar compañeros
if st.session_state.rol_usuario == 'Admin':
    with tabs[4]:
        st.header("👥 Solicitudes Pendientes")
        conn = obtener_conexion()
        df_pendientes = pd.read_sql("SELECT nombre_usuario FROM usuarios WHERE estado = 'Pendiente'", conn)
        
        if not df_pendientes.empty:
            for _, row in df_pendientes.iterrows():
                u_nom = row['nombre_usuario']
                col1, col2 = st.columns([6, 2])
                col1.write(f"👤 **{u_nom}** quiere entrar.")
                if col2.button("✅ Aprobar", key=f"ap_{u_nom}"):
                    cur = conn.cursor()
                    cur.execute("UPDATE usuarios SET estado = 'Aprobado' WHERE nombre_usuario = %s", (u_nom,))
                    conn.commit()
                    st.rerun()
        else:
            st.info("No hay nadie esperando aprobación.")
        conn.close()
