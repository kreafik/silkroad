# -*- coding: utf-8 -*-
"""xFerryFollow 0.1.0 - phBot icin tek dugmeli takip ve ferry gecisi.

KURULUM
  1. Bu dosyayi phBot'un Plugins klasorune koyup phBot'u yeniden baslatin.
  2. Plugins > Ferry Follow sekmesini acin.
  3. Ferry'nin yaninda, 'Gecisi ogren' dugmesine basin. Oyun istemcisinden
     ferry'yi normal sekilde kullanip karsiya gecin. Basarili gecis kaydedilir.
  4. Takip edilecek karakterin tam adini yazin. 'Ferry sonrasi kervan
     petinden in' secenegini tercihinize gore ayarlayip 'Baslat'a basin.
     Sonraki kullanimlarda tek Baslat/Durdur dugmesi yeterlidir.

CALISMA
  - Native phBot trace ile takip eder. Kayitli ferry'ye 25 oyun birimi kala
    trace durur; NPC'ye yaklasilir, NPC secimi sunucudan dogrulanir ve gecilir.
  - Teleported olayi VE kayitli varis konumu dogrulandiktan sonra ayni isimle
    trace yeniden baslatilir. Lider gorunmezse tekrar denemek icin bekler.
  - 'Ferry sonrasi kervan petinden in' varsayilan olarak kapalidir. Acilirsa
    yalnizca transport turundeki binili kervan petine inme istegi gonderilir;
    mounted=False dogrulaninca takip surer. Pet kaybolursa basari varsayilmaz.
    Petin cagirilmasi veya tasidigi mallar icin bir islem yapilmaz.
  - Varis noktasindan 45 birim uzaklasana kadar yeni ferry tetiklenmez;
    karsi kiyida beklerken geri donus dongusu olusmaz.
  - Ters yon de isteniyorsa onu da ayri bir elle gecisle ogretin.
    Ayni kaynak ferry icin yeni hedef ogretmek onceki hedefi gunceller.
  - Ayni phBot/Config klasorunu ve sunucuyu kullanan karakterler gecisleri
    ortak okur. 'Gecisleri yenile' veya Baslat ortak kayitlari yeniden okur.
    Her karakterin lider ismi ayridir. Ayri kurulumlarda yeniden ogretin.
  - Olum, baglanti kopmasi, hata veya Durdur sonrasinda kendiliginden baslamaz.
  - Bu surumde 'tek tus', eklenti sekmesindeki Baslat/Durdur dugmesidir;
    global klavye kisayolu tanimlanmaz.

UYUMLULUK VE TEST
  - Standart 0x705A / <IBI> NPC teleport istegi desteklenir. Ozel ferry
    paketleri/dialog akislari icin bu surum uyarlanmalidir. Elle ogretmek
    icin oyun istemcisi gerekir; sonrasinda native trace'e dayanir.
  - phBot'un Plugins/Python destegi gerekir. Harici Python paketi gerekmez.
  - Ayni karakterde baska takip, hareket, town-loop veya Conditions
    otomasyonu eszamanli komut vermemelidir. Baslat normal botu durdurur.
  - Ilk denemede lider karsi kiyida gorus mesafesinde beklesin.
  - Durum makinesi sahte phBot API'si ile test edildi; canli phBot/HitSRO
    uyumlulugu bu ortamda test edilmedi. Bu ilk deneme surumudur.

KAYNAKLAR
  https://plugins.phbot.org/phbot-api/botting
  https://plugins.phbot.org/phbot-api/events
  https://plugins.phbot.org/phbot-api/npc
  https://plugins.phbot.org/phbot-api/teleport
  https://plugins.phbot.org/gui-api
  Paket yapisi referansi: JellyBitz/phBot-xPlugins, xControl.py,
  b37807cb87330b2a70e5ec18ff8520a0d4171ed9. Uygulama yeni yazilmistir.
"""

import hashlib
import json
import math
import os
import struct
import time

import phBot
import QtBind

pName = 'xFerryFollow'
pVersion = '0.1.0'

DETECT_RADIUS = 25.0
INTERACT_RADIUS = 8.0
NPC_MATCH_RADIUS = 5.0
ARRIVAL_RADIUS = 40.0
LANDING_LOCK_RADIUS = 45.0
APPROACH_TIMEOUT = 30.0
SELECT_TIMEOUT = 8.0
TELEPORT_TIMEOUT = 45.0
VERIFY_TIMEOUT = 10.0
TRACE_INTERVAL = 3.0

gui = QtBind.init(__name__, 'Ferry Follow')
QtBind.createLabel(gui, 'Takip edilecek karakter:', 10, 12)
edtLeader = QtBind.createLineEdit(gui, '', 165, 10, 180, 22)
btnToggle = QtBind.createButton(gui, 'toggle_clicked', '    Baslat    ', 360, 9)
lblStatus = QtBind.createLabel(gui, 'Durum: Kapali', 10, 43)
cbxDismount = QtBind.createCheckBox(gui, 'dismount_changed', 'Ferry sonrasi kervan petinden in', 10, 69)
QtBind.createLabel(gui, 'Kapali: binek durumunu koru', 350, 72)
QtBind.createLabel(gui, 'Bir kez: Gecisi ogren > ferry ile ELLE karsiya gec > Baslat.', 10, 98)
btnLearn = QtBind.createButton(gui, 'learn_clicked', ' Gecisi ogren ', 10, 122)
QtBind.createButton(gui, 'reload_clicked', ' Gecisleri yenile ', 150, 122)
QtBind.createButton(gui, 'remove_clicked', ' Secili gecisi sil ', 320, 122)
lstRoutes = QtBind.createList(gui, 10, 152, 650, 70)
QtBind.createLabel(gui, 'Gecis kayitlari ayni kurulumda sunucu bazinda ortaktir.', 10, 230)
QtBind.createLabel(gui, 'Ilk denemede lider karsi kiyida beklesin. Sorun ayrintisi Log sekmesindedir.', 10, 252)

_mode = 'IDLE'
_identity = None
_ready = False
_leader = ''
_dismount_after = False
_settings_loading = False
_routes = []
_pending = None
_deadline = 0.0
_next_trace = 0.0
_next_move = 0.0
_selected = False
_selected_at = 0.0
_verify_after = 0.0
_landing_lock = None
_last_status = ''


def _now():
    return time.monotonic()


def _status(text):
    global _last_status
    QtBind.setText(gui, lblStatus, 'Durum: ' + text)
    if text != _last_status:
        phBot.log('[%s] %s' % (pName, text))
        _last_status = text


def _point(data):
    if not isinstance(data, dict):
        return None
    try:
        p = {'region': int(data['region']), 'x': float(data['x']),
             'y': float(data['y']), 'z': float(data.get('z', 0.0))}
        if not all(math.isfinite(p[k]) for k in ('x', 'y', 'z')):
            return None
        return p
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def _distance(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def _character():
    data = phBot.get_character_data()
    if not data or not data.get('name') or not data.get('server'):
        return None
    return data


def _key(data):
    return (data['server'], data['name'], int(phBot.get_locale()))


def _file(kind):
    if _identity is None:
        raise ValueError('Karakter bilgisi henuz hazir degil.')
    server, character, locale = _identity
    scope = [server, locale] if kind == 'routes' else [server, locale, character]
    digest = hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]
    folder = os.path.join(phBot.get_config_dir(), pName)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, kind + '_' + digest + '.json')


def _read(path, default):
    if not os.path.exists(path):
        return default
    with open(path, 'r', encoding='utf-8') as stream:
        return json.load(stream)


def _write(path, value):
    temporary = path + '.%d.tmp' % os.getpid()
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _valid_route(route):
    if not isinstance(route, dict):
        return False
    source = route.get('source')
    destination = route.get('destination')
    if not _point(source) or not _point(destination):
        return False
    return (isinstance(route.get('id'), str) and bool(route['id']) and
            isinstance(source.get('servername'), str) and bool(source['servername']) and
            isinstance(source.get('name'), str) and
            isinstance(route.get('destination_id'), int) and
            0 < route['destination_id'] < 4294967296)


def _read_routes():
    payload = _read(_file('routes'), {'schema': 1, 'routes': []})
    if not isinstance(payload, dict) or payload.get('schema') != 1:
        raise ValueError('Gecis kaydi bicimi taninmiyor.')
    routes = payload.get('routes')
    if not isinstance(routes, list) or not all(_valid_route(r) for r in routes):
        raise ValueError('Gecis kaydinda eksik veya gecersiz alan var.')
    return routes


def _refresh_routes():
    global _routes
    _routes = _read_routes()
    _render_routes()


def _render_routes():
    QtBind.clear(gui, lstRoutes)
    for route in _routes:
        source = route['source']
        destination = route['destination']
        QtBind.append(gui, lstRoutes, '%s (%.0f, %.0f) > (%.0f, %.0f)' %
                      (source['name'], source['x'], source['y'],
                       destination['x'], destination['y']))


def _load_character():
    global _identity, _leader, _routes, _dismount_after, _settings_loading
    data = _character()
    if not data:
        return False
    identity = _key(data)
    if identity == _identity:
        return True
    _stop('Karakter ayarlari yukleniyor.')
    _identity = identity
    _leader = ''
    _dismount_after = False
    _routes = []
    _settings_loading = True
    QtBind.setText(gui, edtLeader, '')
    QtBind.setChecked(gui, cbxDismount, False)
    _render_routes()
    try:
        settings = _read(_file('character'), {})
        if not isinstance(settings, dict) or not isinstance(settings.get('leader', ''), str):
            raise ValueError('Lider ayari okunamadi.')
        _leader = settings.get('leader', '')
        _dismount_after = settings.get('dismount_after', False)
        if not isinstance(_dismount_after, bool):
            raise ValueError('Pet secenegi gecersiz.')
        QtBind.setText(gui, edtLeader, _leader)
        QtBind.setChecked(gui, cbxDismount, _dismount_after)
        _refresh_routes()
        _status('Hazir. %d gecis kayitli.' % len(_routes))
    except Exception as error:
        _status('Ayar yukleme hatasi: ' + str(error))
    finally:
        _settings_loading = False
    return True


def _stop(message):
    global _mode, _pending, _selected, _deadline
    was_approaching = _mode == 'APPROACH'
    active = _mode not in ('IDLE', 'LEARN', 'LEARN_WAIT', 'LEARN_VERIFY')
    _mode = 'IDLE'
    _pending = None
    _selected = False
    _deadline = 0.0
    if active:
        phBot.stop_trace()
        # Cancel an outstanding short move to the source NPC when possible.
        position = _point(phBot.get_position()) if _ready and was_approaching else None
        if position:
            phBot.move_to(position['x'], position['y'], position['z'])
    QtBind.setText(gui, btnToggle, '    Baslat    ')
    QtBind.setText(gui, btnLearn, ' Gecisi ogren ')
    _status(message)


def _ensure_idle():
    if _mode != 'IDLE':
        _status('Once Durdur veya Ogrenmeyi iptal dugmesine basin.')
        return False
    if not _ready or not _load_character():
        _status('Karakterin oyuna girmesini bekleyin.')
        return False
    return True


def toggle_clicked():
    global _mode, _leader, _next_trace, _dismount_after
    try:
        if _mode != 'IDLE':
            _stop('Durduruldu.')
            return
        if not _ensure_idle():
            return
        leader = QtBind.text(gui, edtLeader).strip()
        if not leader or leader.casefold() == _identity[1].casefold():
            _status('Takip edilecek diger karakterin tam adini yazin.')
            return
        _refresh_routes()
        if not _routes:
            _status('Once ferry gecisini bir kez ogretin.')
            return
        _dismount_after = QtBind.isChecked(gui, cbxDismount)
        _write(_file('character'), {'schema': 1, 'leader': leader,
                                  'dismount_after': _dismount_after})
        _leader = leader
        phBot.stop_bot()
        phBot.stop_trace()
        _mode = 'FOLLOW'
        _next_trace = 0.0
        QtBind.setText(gui, btnToggle, '    Durdur    ')
        _status('Takip basliyor: ' + _leader)
        _tick()
    except Exception as error:
        _stop('Baslatma hatasi: ' + str(error))


def dismount_changed(checked):
    global _dismount_after
    if _settings_loading:
        return
    _dismount_after = bool(checked)
    try:
        if _ready and _identity:
            leader = _leader if _mode != 'IDLE' else QtBind.text(gui, edtLeader).strip()
            _write(_file('character'), {'schema': 1, 'leader': leader,
                                      'dismount_after': _dismount_after})
    except Exception as error:
        _status('Pet tercihi kaydedilemedi: ' + str(error))


def learn_clicked():
    global _mode, _pending, _deadline
    try:
        if _mode.startswith('LEARN'):
            _stop('Ogrenme iptal edildi.')
            return
        if not _ensure_idle():
            return
        phBot.stop_bot()
        phBot.stop_trace()
        _pending = None
        _mode = 'LEARN'
        _deadline = _now() + 180.0
        QtBind.setText(gui, btnLearn, ' Ogrenmeyi iptal ')
        _status('Oyun istemcisinde ferry ile ELLE karsiya gecin (3 dakika).')
    except Exception as error:
        _stop('Ogrenme baslatilamadi: ' + str(error))


def reload_clicked():
    try:
        if _ensure_idle():
            _refresh_routes()
            _status('%d gecis yuklendi.' % len(_routes))
    except Exception as error:
        _stop('Gecisler okunamadi: ' + str(error))


def remove_clicked():
    global _routes
    try:
        if not _ensure_idle():
            return
        index = QtBind.currentIndex(gui, lstRoutes)
        if index < 0 or index >= len(_routes):
            _status('Silinecek gecisi listeden secin.')
            return
        route_id = _routes[index]['id']
        routes = [r for r in _read_routes() if r['id'] != route_id]
        _write(_file('routes'), {'schema': 1, 'routes': routes})
        _routes = routes
        _render_routes()
        _status('Secili gecis silindi.')
    except Exception as error:
        _stop('Gecis silinemedi: ' + str(error))


def _route_npc(route):
    source = route['source']
    for uid, npc in (phBot.get_npcs() or {}).items():
        point = _point(npc)
        if (point and npc.get('servername') == source['servername'] and
                point['region'] == source['region'] and
                _distance(point, source) <= NPC_MATCH_RADIUS):
            return int(uid), npc
    return None


def _begin_approach(route, position):
    global _mode, _pending, _deadline, _next_move
    phBot.stop_trace()
    _pending = {'route': route, 'origin': position}
    _mode = 'APPROACH'
    _deadline = _now() + APPROACH_TIMEOUT
    _next_move = 0.0
    _status('Ferryye yaklasiliyor: ' + route['source']['name'])


def _resume_follow():
    global _mode, _pending, _next_trace
    _pending = None
    _mode = 'FOLLOW'
    _next_trace = 0.0
    _status('Takip yeniden baslatiliyor: ' + _leader)


def _dismount_tick(now):
    if now < _pending['pet_settle_until']:
        return
    pets = phBot.get_pets()
    if pets is None:
        return
    requested_uid = _pending.get('dismount_uid')
    if requested_uid is None and not _dismount_after:
        _resume_follow()
        return
    if requested_uid is not None:
        pet = pets.get(requested_uid)
        # An absent pet record during loading is not proof of dismounting.
        if pet and pet.get('type') == 'transport' and pet.get('mounted') is False:
            _resume_follow()
        return
    transports = [(int(uid), pet) for uid, pet in pets.items()
                  if pet.get('type') == 'transport']
    for uid, pet in transports:
        if pet.get('mounted') is True:
            _pending['dismount_uid'] = uid
            phBot.inject_joymax(0x70CB, struct.pack('<BI', 0, uid), False)
            _status('Kervan petinden iniliyor; sonuc bekleniyor.')
            return
    if transports and all(pet.get('mounted') is False for uid, pet in transports):
        _resume_follow()
    elif not transports and _pending.get('transport_before') is False:
        _resume_follow()


def _learn_packet(data):
    global _mode, _pending, _deadline
    if len(data) != 9 or data[4] != 2:
        _stop('Bu ferry paketi standart degil. Sunucuya ozel uyarlama gerekiyor.')
        return
    uid, action, destination_id = struct.unpack('<IBI', data)
    npcs = phBot.get_npcs() or {}
    npc = npcs.get(uid)
    position = _point(phBot.get_position())
    source = _point(npc)
    if (not source or not position or not npc.get('servername') or
            _distance(source, position) > 35.0 or destination_id == 0):
        _stop('Kaynak NPC dogrulanamadi. Ferryye yaklasip yeniden ogretin.')
        return
    source.update({'name': str(npc.get('name') or npc['servername']),
                   'servername': str(npc['servername'])})
    _pending = {'source': source, 'destination_id': destination_id,
                'origin': position}
    _mode = 'LEARN_WAIT'
    _deadline = _now() + TELEPORT_TIMEOUT
    _status('Gecis istegi alindi; karsi kiyinin yuklenmesi bekleniyor.')


def _finish_learning(position):
    global _routes, _landing_lock
    candidate = _pending
    origin = candidate['origin']
    if position['region'] == origin['region'] and _distance(position, origin) < 15.0:
        return False
    route = {'source': candidate['source'], 'destination': position,
             'destination_id': candidate['destination_id']}
    identity = [route['source']['servername'], route['source']['region'],
                round(route['source']['x']), round(route['source']['y']),
                route['destination_id']]
    route['id'] = hashlib.sha256(json.dumps(identity).encode('utf-8')).hexdigest()[:20]
    # One outgoing destination per source; an equally near route must never
    # silently win by list order.
    routes = [r for r in _read_routes() if not (
        r['source']['servername'] == route['source']['servername'] and
        r['source']['region'] == route['source']['region'] and
        _distance(r['source'], route['source']) <= NPC_MATCH_RADIUS)]
    routes.append(route)
    _write(_file('routes'), {'schema': 1, 'routes': routes})
    _routes = routes
    _render_routes()
    _landing_lock = dict(position)
    _stop('Gecis kaydedildi. Lider adini yazip Baslat dugmesine basin.')
    return True


def _tick():
    global _mode, _pending, _deadline, _next_trace, _next_move
    global _selected, _selected_at, _landing_lock
    if _mode == 'IDLE':
        return
    now = _now()
    data = _character()
    if not data or _key(data) != _identity:
        _stop('Karakter degisti veya baglanti kesildi.')
        return
    if data.get('dead') or data.get('hp') == 0:
        _stop('Karakter oldu; takip durduruldu.')
        return
    position = _point(phBot.get_position())
    if _mode.startswith('LEARN'):
        if now >= _deadline:
            _stop('Ogrenme zaman asimi. Gecis kaydedilmedi.')
        elif _mode == 'LEARN_VERIFY' and now >= _verify_after and position:
            _finish_learning(position)
        return
    # A missing position during loading must not suppress the timeout.
    if _mode in ('APPROACH', 'SELECT', 'WAIT_TELEPORT', 'VERIFY', 'DISMOUNT') and now >= _deadline:
        if _mode == 'DISMOUNT':
            _stop('Petten inme dogrulanamadi. Peti kontrol edip yeniden Baslat.')
        else:
            _stop('Gecis dogrulanamadi (%s). Logu kontrol edip yeniden Baslat.' % _mode)
        return
    if not position:
        return
    if _mode == 'VERIFY':
        if now < _verify_after:
            return
        expected = _pending['route']['destination']
        origin = _pending['origin']
        moved = (position['region'] != origin['region'] or _distance(position, origin) >= 15.0)
        # XY is global outdoors; exact character region at a sector boundary
        # can differ by one sector from the original arrival snapshot.
        if moved and _distance(position, expected) <= ARRIVAL_RADIUS:
            _landing_lock = dict(position)
            if _dismount_after:
                _mode = 'DISMOUNT'
                _pending['pet_settle_until'] = now + 1.5
                _pending['dismount_uid'] = None
                _deadline = now + 15.0
                _status('Karsiya gecildi; kervan peti kontrol ediliyor.')
            else:
                _resume_follow()
        else:
            return
    if _mode == 'WAIT_TELEPORT':
        return
    if _mode == 'DISMOUNT':
        _dismount_tick(now)
        return
    if _mode == 'FOLLOW':
        if _landing_lock and _distance(position, _landing_lock) > LANDING_LOCK_RADIUS:
            _landing_lock = None
        if _landing_lock is None:
            candidates = []
            for route in _routes:
                match = _route_npc(route)
                if match:
                    distance = _distance(position, route['source'])
                    if distance <= DETECT_RADIUS:
                        candidates.append((distance, route))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                _begin_approach(candidates[0][1], position)
                return
        if now >= _next_trace:
            _next_trace = now + TRACE_INTERVAL
            if phBot.start_trace(_leader):
                _status('Takip ediliyor: ' + _leader)
            else:
                _status('Lider bekleniyor: ' + _leader)
        return
    match = _route_npc(_pending['route'])
    if not match:
        _stop('Kayitli ferry NPCsi artik gorunmuyor; takip durduruldu.')
        return
    uid, npc = match
    npc_position = _point(npc)
    if _mode == 'APPROACH':
        if _distance(position, npc_position) <= INTERACT_RADIUS:
            _mode = 'SELECT'
            _selected = False
            _selected_at = now
            _pending['npc_uid'] = uid
            _deadline = now + SELECT_TIMEOUT
            phBot.inject_joymax(0x7045, struct.pack('<I', uid), False)
            _status('Ferry NPC secimi bekleniyor.')
        elif now >= _next_move:
            _next_move = now + 2.0
            phBot.move_to(npc_position['x'], npc_position['y'], npc_position['z'])
        return
    if _mode == 'SELECT':
        if uid != _pending['npc_uid']:
            _stop('NPC kimligi degisti; tekrar Baslat dugmesine basin.')
            return
        if _distance(position, npc_position) > INTERACT_RADIUS + 2.0:
            _stop('Gecis sirasinda NPCden uzaklasildi.')
            return
        if _selected and now >= _selected_at + 0.5:
            destination_id = _pending['route']['destination_id']
            pets_before = phBot.get_pets()
            _pending['transport_before'] = (None if pets_before is None else any(
                pet.get('type') == 'transport' for pet in pets_before.values()))
            _mode = 'WAIT_TELEPORT'
            _deadline = now + TELEPORT_TIMEOUT
            phBot.inject_joymax(0x705A, struct.pack('<IBI', uid, 2, destination_id), False)
            _status('Ferry gecisi bekleniyor.')


def handle_silkroad(opcode, data):
    try:
        if _mode == 'LEARN' and opcode == 0x705A:
            _learn_packet(bytes(data))
    except Exception as error:
        _stop('Gecis ogrenilemedi: ' + str(error))
    return True


def handle_joymax(opcode, data):
    global _selected
    try:
        if _mode == 'SELECT' and opcode == 0xB045 and len(data) >= 5 and data[0] == 1:
            if struct.unpack_from('<I', bytes(data), 1)[0] == _pending['npc_uid']:
                _selected = True
    except Exception as error:
        _stop('NPC secimi okunamadi: ' + str(error))
    return True


def teleported():
    global _mode, _ready, _verify_after, _deadline, _next_trace
    _ready = True
    now = _now()
    if _mode == 'LEARN_WAIT':
        _mode = 'LEARN_VERIFY'
        _verify_after = now + 1.0
        _deadline = now + VERIFY_TIMEOUT
    elif _mode == 'WAIT_TELEPORT':
        _mode = 'VERIFY'
        _verify_after = now + 1.0
        _deadline = now + VERIFY_TIMEOUT
    elif _mode in ('APPROACH', 'SELECT', 'DISMOUNT'):
        _stop('Beklenmeyen teleport; takip durduruldu.')
    elif _mode == 'FOLLOW':
        _next_trace = now + 1.0


def connected():
    global _ready, _identity, _landing_lock
    _ready = False
    _stop('Baglanti kuruluyor; otomatik baslama kapali.')
    _identity = None
    _landing_lock = None


def disconnected():
    global _ready
    _ready = False
    _stop('Baglanti kesildi; takip durduruldu.')


def joined_game():
    global _ready, _identity, _landing_lock
    _ready = False
    _stop('Karakter yukleniyor.')
    _identity = None
    _landing_lock = None


def handle_event(event_type, data):
    if event_type == 7 and _mode != 'IDLE':
        _stop('Karakter oldu; takip durduruldu.')


def event_loop():
    try:
        if _ready and _load_character():
            _tick()
    except Exception as error:
        _stop('Hata nedeniyle durduruldu: ' + str(error))


def finished():
    global _ready
    _ready = False
    _stop('Eklenti kapatildi.')


try:
    _ready = bool(_character() and _point(phBot.get_position()))
    if _ready:
        _load_character()
    phBot.log('[%s] v%s yuklendi. Ilk deneme surumu; once bir gecis ogretin.' % (pName, pVersion))
except Exception as _initial_error:
    _status('Baslangic hatasi: ' + str(_initial_error))
