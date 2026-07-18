
# shared one-shot event dispatch.
#
# the authoritative sim emits presentation events (world.emit) when momentary
# things happen — an entity swings, a hit lands. those aren't continuous state,
# so they don't belong in the position/hp snapshot; they ride a separate event
# stream instead. this module turns one event into its local presentation
# (animation swing, dust, floating number).
#
# both single-player (game.py drains world.events directly) and the net client
# (client.py drains events off each snapshot) route through apply(), so an
# effect implemented once shows up in both. the server itself never calls this —
# it only serializes the events onto the wire.


def apply(world, break_system, combat, event, now_ms: int) -> None:
    # dispatch one event to its local effect. unknown kinds are ignored so a
    # newer server can add event kinds without breaking an older client.
    kind = event.get('kind')
    if kind == 'attack':
        # one-shot swing on the acting entity, facing left/right. a missing
        # entity (despawned) or missing state is a safe no-op.
        ent = world.entities.get(event.get('id'))
        if ent is not None and ent.anim is not None:
            ent.anim.play_once('attacking_' + event.get('facing', 'right'), now_ms)
    elif kind == 'hit':
        # a hit landed on a target: kick up dust at its feet and float the
        # damage number above it. combat may be None on a headless consumer.
        break_system.spawn_dust((event['x'], event.get('fy', event['y'])), now_ms)
        if combat is not None and 'amount' in event:
            combat.spawn_number(event['x'], event['y'], event['amount'], now_ms)
