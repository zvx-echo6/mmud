"""
Admin routes — behind session-based auth.
"""
import functools
import json
import os
import queue

from flask import (
    Blueprint, Response, current_app, flash, jsonify, redirect,
    render_template, request, session, url_for,
)

from src.web import config as web_config
from src.web.services import gamedb
from src.web.services import admin_service as admin_svc
from src.web.services import epoch_service

bp = Blueprint("admin", __name__, template_folder="../templates/admin")


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == web_config.WEB_ADMIN_PASSWORD:
            session["admin_authenticated"] = True
            session["admin_user"] = request.form.get("callsign", "operator")
            return redirect(url_for("admin.dashboard"))
        return render_template("admin/login.html", error="Invalid credentials.")
    return render_template("admin/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("public.index"))


@bp.route("/")
@login_required
def dashboard():
    epoch = gamedb.get_epoch_status()
    player_count = gamedb.get_player_count()
    log = gamedb.get_admin_log(limit=10)
    nodes = gamedb.get_node_config()
    # Merge live transport status
    router = current_app.config.get("NODE_ROUTER")
    for node in nodes:
        role_upper = node["role"].upper()
        if router and role_upper in router.transports:
            node["connected"] = router.transports[role_upper]._interface is not None
        else:
            node["connected"] = False
    return render_template(
        "admin/dashboard.html",
        epoch=epoch,
        player_count=player_count,
        log=log,
        nodes=nodes,
    )


@bp.route("/nodes")
@login_required
def nodes():
    nodes = gamedb.get_node_config()
    # Merge live transport status from the router
    router = current_app.config.get("NODE_ROUTER")
    for node in nodes:
        role_upper = node["role"].upper()
        if router and role_upper in router.transports:
            transport = router.transports[role_upper]
            node["connected"] = transport._interface is not None
            node["live_node_id"] = transport.my_node_id
        else:
            node["connected"] = False
            node["live_node_id"] = None
    return render_template("admin/nodes.html", nodes=nodes)


@bp.route("/nodes/<role>")
@login_required
def node_detail(role):
    nodes = gamedb.get_node_config()
    node = None
    for n in nodes:
        if n["role"] == role:
            node = n
            break
    if not node:
        flash("Unknown node role.", "error")
        return redirect(url_for("admin.nodes"))

    role_upper = role.upper()
    router = current_app.config.get("NODE_ROUTER")
    transport = router.transports.get(role_upper) if router else None
    node["connected"] = transport._interface is not None if transport else False
    node["live_node_id"] = transport.my_node_id if transport else None

    # Read live Meshtastic config from the device
    mesh_config = None
    if transport and node["connected"]:
        try:
            mesh_config = transport.get_node_config()
        except Exception as e:
            flash(f"Could not read device config: {e}", "error")

    # Message count for this node
    message_count = gamedb.get_node_message_count(role_upper)

    return render_template(
        "admin/node_detail.html",
        node=node,
        mesh_config=mesh_config,
        message_count=message_count,
    )


@bp.route("/nodes/<role>/connection", methods=["POST"])
@login_required
def node_update_connection(role):
    connection = request.form.get("connection", "").strip()
    admin_svc.update_node_connection(session["admin_user"], role, connection)
    flash("Connection updated. Restart container to apply.", "success")
    return redirect(url_for("admin.node_detail", role=role))


@bp.route("/nodes/<role>/identity", methods=["POST"])
@login_required
def node_update_identity(role):
    role_upper = role.upper()
    router = current_app.config.get("NODE_ROUTER")
    transport = router.transports.get(role_upper) if router else None
    if not transport or not transport._interface:
        flash("Node not connected.", "error")
        return redirect(url_for("admin.node_detail", role=role))

    long_name = request.form.get("long_name", "").strip()
    short_name = request.form.get("short_name", "").strip()
    if not long_name or not short_name:
        flash("Both long name and short name are required.", "error")
        return redirect(url_for("admin.node_detail", role=role))

    try:
        transport.set_owner(long_name, short_name)
        flash("Identity updated.", "success")
    except Exception as e:
        flash(f"Failed to set identity: {e}", "error")
    return redirect(url_for("admin.node_detail", role=role))


@bp.route("/nodes/<role>/channel", methods=["POST"])
@login_required
def node_update_channel(role):
    role_upper = role.upper()
    router = current_app.config.get("NODE_ROUTER")
    transport = router.transports.get(role_upper) if router else None
    if not transport or not transport._interface:
        flash("Node not connected.", "error")
        return redirect(url_for("admin.node_detail", role=role))

    index = int(request.form.get("index", 0))
    name = request.form.get("name")
    psk_hex = request.form.get("psk_hex")

    # Only pass values that were actually submitted
    kwargs = {}
    if name is not None and name != "":
        kwargs["name"] = name
    if psk_hex is not None:
        kwargs["psk_hex"] = psk_hex

    if not kwargs:
        flash("No channel changes submitted.", "error")
        return redirect(url_for("admin.node_detail", role=role))

    try:
        transport.set_channel(index, **kwargs)
        flash(f"Channel {index} updated.", "success")
    except Exception as e:
        flash(f"Failed to set channel: {e}", "error")
    return redirect(url_for("admin.node_detail", role=role))


@bp.route("/nodes/<role>/radio", methods=["POST"])
@login_required
def node_update_radio(role):
    role_upper = role.upper()
    router = current_app.config.get("NODE_ROUTER")
    transport = router.transports.get(role_upper) if router else None
    if not transport or not transport._interface:
        flash("Node not connected.", "error")
        return redirect(url_for("admin.node_detail", role=role))

    lora_kwargs = {}
    modem_preset = request.form.get("modem_preset")
    if modem_preset is not None and modem_preset != "":
        lora_kwargs["modem_preset"] = int(modem_preset)
    tx_power = request.form.get("tx_power")
    if tx_power is not None and tx_power != "":
        lora_kwargs["tx_power"] = int(tx_power)
    region = request.form.get("region")
    if region is not None and region != "":
        lora_kwargs["region"] = int(region)
    channel_num = request.form.get("channel_num")
    if channel_num is not None and channel_num != "":
        lora_kwargs["channel_num"] = int(channel_num)

    if not lora_kwargs:
        flash("No radio changes submitted.", "error")
        return redirect(url_for("admin.node_detail", role=role))

    try:
        transport.set_lora(**lora_kwargs)
        flash("Radio settings updated.", "success")
    except Exception as e:
        flash(f"Failed to set radio: {e}", "error")
    return redirect(url_for("admin.node_detail", role=role))


@bp.route("/nodes/<role>/log")
@login_required
def node_log(role):
    nodes = gamedb.get_node_config()
    node = None
    for n in nodes:
        if n["role"] == role:
            node = n
            break
    if not node:
        flash("Unknown node role.", "error")
        return redirect(url_for("admin.nodes"))
    role_upper = role.upper()
    messages = gamedb.get_node_messages(role_upper)
    return render_template(
        "admin/node_log.html",
        node=node,
        messages=messages,
    )


@bp.route("/nodes/<role>/log/api")
@login_required
def node_log_api(role):
    role_upper = role.upper()
    after_id = request.args.get("after", 0, type=int)
    messages = gamedb.get_node_messages_after(role_upper, after_id)
    return jsonify(messages)


@bp.route("/log")
@login_required
def log():
    messages = gamedb.get_all_messages()
    return render_template("admin/log.html", messages=messages)


@bp.route("/log/api")
@login_required
def log_api():
    after_id = request.args.get("after", 0, type=int)
    messages = gamedb.get_all_messages_after(after_id)
    return jsonify(messages)


@bp.route("/players")
@login_required
def players():
    players = gamedb.get_player_list()
    return render_template("admin/players.html", players=players)


@bp.route("/players/<int:player_id>/ban", methods=["POST"])
@login_required
def ban_player(player_id):
    reason = request.form.get("reason", "")
    admin_svc.ban_player(session["admin_user"], player_id, reason)
    flash("Player banned.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:player_id>/kick", methods=["POST"])
@login_required
def kick_player(player_id):
    admin_svc.kick_player(session["admin_user"], player_id)
    flash("Player kicked to town.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/players/<int:player_id>/reset", methods=["POST"])
@login_required
def reset_player(player_id):
    admin_svc.reset_player(session["admin_user"], player_id)
    flash("Player reset to level 1.", "success")
    return redirect(url_for("admin.players"))


@bp.route("/join")
@login_required
def join_config():
    config = admin_svc.get_join_config()
    return render_template("admin/join.html", config=config)


@bp.route("/join", methods=["POST"])
@login_required
def join_config_save():
    admin_svc.save_join_config(
        session["admin_user"],
        channel_name=request.form.get("channel_name", "").strip(),
        channel_psk=request.form.get("channel_psk", "").strip(),
        modem_preset=request.form.get("modem_preset", "LONG_FAST").strip(),
        region=request.form.get("region", "US").strip(),
        channel_num=int(request.form.get("channel_num", 0)),
        game_node_name=request.form.get("game_node_name", "EMBR").strip(),
        custom_instructions=request.form.get("custom_instructions", "").strip(),
    )
    flash("Join configuration saved.", "success")
    return redirect(url_for("admin.join_config"))


@bp.route("/epoch")
@login_required
def epoch():
    epoch_data = gamedb.get_epoch_status()
    breach = gamedb.get_breach_status()

    # Detect backend
    db_path = web_config.DB_PATH
    try:
        from src.generation.narrative import get_backend
        backend = get_backend(db_path=db_path)
        backend_name = type(backend).__name__
        backend_ready = "Dummy" not in backend_name
    except Exception as e:
        backend_name = f"Error: {e}"
        backend_ready = False

    # Check if generation is in progress
    gen_running = epoch_service.is_running()
    gen_result = epoch_service.get_result()

    return render_template(
        "admin/epoch.html",
        epoch=epoch_data,
        breach=breach,
        backend_name=backend_name,
        backend_ready=backend_ready,
        gen_running=gen_running,
        gen_result=gen_result,
    )


@bp.route("/epoch/generate", methods=["POST"])
@login_required
def epoch_generate():
    if epoch_service.is_running():
        return jsonify({"error": "Generation already in progress"}), 409

    db_path = web_config.DB_PATH
    endgame_mode = request.form.get("endgame_mode", "").strip()
    breach_type = request.form.get("breach_type", "").strip()

    # Determine epoch number
    epoch_data = gamedb.get_epoch_status()
    epoch_number = (epoch_data["epoch_number"] + 1) if epoch_data else 1

    started = epoch_service.start_generation(
        db_path=db_path,
        epoch_number=epoch_number,
        endgame_mode=endgame_mode,
        breach_type=breach_type,
        admin_user=session.get("admin_user", "operator"),
    )

    if not started:
        return jsonify({"error": "Generation already in progress"}), 409

    return jsonify({"status": "started", "epoch_number": epoch_number}), 202


@bp.route("/epoch/generate/stream")
@login_required
def epoch_generate_stream():
    log_queue = epoch_service.get_log_queue()

    def event_stream():
        while True:
            try:
                msg = log_queue.get(timeout=30)
                if msg is None:
                    result = epoch_service.get_result()
                    yield f"data: {json.dumps({'type': 'complete', 'result': result})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@bp.route("/epoch/advance-day", methods=["POST"])
@login_required
def advance_day():
    new_day = admin_svc.advance_day(session["admin_user"])
    flash(f"Advanced to day {new_day}.", "success")
    return redirect(url_for("admin.epoch"))


@bp.route("/epoch/force-breach", methods=["POST"])
@login_required
def force_breach():
    admin_svc.force_breach(session["admin_user"])
    flash("Breach opened.", "success")
    return redirect(url_for("admin.epoch"))


@bp.route("/broadcast", methods=["POST"])
@login_required
def broadcast():
    message = request.form.get("message", "").strip()
    tier = int(request.form.get("tier", 1))
    if not message:
        flash("Message required.", "error")
        return redirect(url_for("admin.epoch"))
    admin_svc.send_broadcast(session["admin_user"], message, tier)
    flash("Broadcast sent.", "success")
    return redirect(url_for("admin.epoch"))


@bp.route("/llm")
@login_required
def llm():
    config = admin_svc.get_llm_config()
    # Mask API key for display
    masked_key = ""
    if config.get("api_key"):
        key = config["api_key"]
        masked_key = f"****{key[-4:]}" if len(key) >= 4 else "****"
    return render_template(
        "admin/llm.html",
        config=config,
        masked_key=masked_key,
    )


@bp.route("/llm", methods=["POST"])
@login_required
def llm_save():
    backend = request.form.get("backend", "dummy")
    api_key = request.form.get("api_key", "").strip()
    model = request.form.get("model", "").strip()
    base_url = request.form.get("base_url", "").strip()

    saved = admin_svc.save_llm_config(
        session["admin_user"], backend, api_key, model, base_url,
    )
    admin_svc.apply_llm_config(current_app, saved)
    flash(f"LLM configuration saved: {backend}.", "success")
    return redirect(url_for("admin.llm"))


@bp.route("/llm/test", methods=["POST"])
@login_required
def llm_test():
    backend = request.form.get("backend", "dummy")
    api_key = request.form.get("api_key", "").strip()
    model = request.form.get("model", "").strip()
    base_url = request.form.get("base_url", "").strip()

    success, message = admin_svc.test_llm_connection(
        backend, api_key, model, base_url,
    )
    return jsonify({"success": success, "message": message})


@bp.route("/system")
@login_required
def system():
    db_path = web_config.DB_PATH
    db_size = None
    try:
        db_size = os.path.getsize(db_path)
    except OSError:
        pass
    nodes = gamedb.get_node_config()
    # Merge live transport status
    router = current_app.config.get("NODE_ROUTER")
    for node in nodes:
        role_upper = node["role"].upper()
        if router and role_upper in router.transports:
            node["connected"] = router.transports[role_upper]._interface is not None
        else:
            node["connected"] = False
    log = gamedb.get_admin_log(limit=20)
    return render_template(
        "admin/system.html",
        db_path=db_path,
        db_size=db_size,
        nodes=nodes,
        log=log,
    )
