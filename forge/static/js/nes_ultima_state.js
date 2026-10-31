/* Ultima III (NES) live RAM inspector — ROM Detectives + Data Crystal */
(function (global) {
  const MAPS = {
    0x00:"Sosaria",0x01:"Fawn",0x02:"Cave of Gold",0x03:"Cave of Death",
    0x04:"Moon",0x05:"Yew",0x06:"Castle British",0x07:"Royal City",
    0x08:"Cave of Madness",0x09:"Cave of Moon",0x0A:"Devil Guard",
    0x0B:"Death Gulch",0x0C:"Cave of Fire",0x0D:"Gray",0x0E:"Cave of Sol",
    0x0F:"Ambrosia",0x10:"Cave of Fool",0x11:"Montor West",0x12:"Montor East",
    0x13:"Dawn",0x14:"Castle Exodus",0x15:"Shrine of Int",0x16:"Shrine of Wis",
    0x17:"Shrine of Str",0x18:"Shrine of Dex"
  };
  const CLASSES = {0:"Fighter",1:"Cleric",2:"Wizard",3:"Thief",4:"Paladin",
    5:"Barbarian",6:"Lark",7:"Illusionist",8:"Druid",9:"Alchemist",10:"Ranger"};
  const STATUS = {0:"Good",1:"Poison",2:"Cold",3:"Dead",4:"Ash"};

  function peek(addr) {
    const nes = (global.nesState && global.nesState.nes) || null;
    if (!nes) return 0;
    try {
      if (nes.mmap && typeof nes.mmap.load === "function") {
        const v = nes.mmap.load(addr);
        if (typeof v === "number") return v & 0xFF;
      }
    } catch (_) {}
    try {
      if (nes.cpu && nes.cpu.mem) return nes.cpu.mem[addr & 0xFFFF] & 0xFF;
    } catch (_) {}
    return 0;
  }
  function peek16(addr) { return peek(addr) | (peek(addr + 1) << 8); }
  function nameAt(base) {
    let s = "";
    for (let i = 0; i < 5; i++) {
      const c = peek(base + i);
      if (c >= 32 && c < 127) s += String.fromCharCode(c);
    }
    return s.trim();
  }
  function textWork() {
    let raw = [];
    let s = "";
    for (let i = 0; i < 48; i++) {
      const c = peek(0x0580 + i);
      raw.push(c);
      s += (c >= 32 && c < 127) ? String.fromCharCode(c) : (c ? "." : " ");
    }
    return { ascii: s.trim(), raw: raw.slice(0, 16) };
  }
  function charAt(base) {
    const nm = nameAt(base);
    return {
      name: nm,
      class: CLASSES[peek(base + 0x06)] || peek(base + 0x06),
      str: peek(base + 0x07), dex: peek(base + 0x08),
      int: peek(base + 0x09), wis: peek(base + 0x0A),
      status: STATUS[peek(base + 0x0B)] || peek(base + 0x0B),
      hp: peek16(base + 0x2D), max_hp: peek16(base + 0x36),
      mp: peek(base + 0x2F), max_mp: peek(base + 0x38),
      food: peek16(base + 0x2B), gold: peek16(base + 0x30),
      level: peek(base + 0x33), exp: peek16(base + 0x39),
      marks: peek(base + 0x3B), cards: peek(base + 0x3C),
      horn: peek(base + 0x29), flower: peek(base + 0x3D),
    };
  }
  function readState() {
    const st = global.nesState || {};
    const mapId = peek(0x70);
    const party = [0,1,2,3].map(i => charAt(0x7200 + i * 0x40));
    const named = party.filter(p => p.name);
    const enemies = [];
    for (let i = 0; i < 8; i++) {
      const hp = peek16(0x03F0 + i * 2);
      if (hp) enemies.push(hp);
    }
    const state = {
      frame: st.frameN || 0,
      running: !!st.running,
      map_id: mapId,
      map: MAPS[mapId] || ("map_"+mapId),
      x: peek(0x4A),
      y: peek(0x49),
      pad: peek(0x09),
      in_combat: enemies.length > 0,
      enemies,
      party: named.length ? named : party,
      text: textWork(),
      ram: {
        zp49: peek(0x49), zp4A: peek(0x4A), zp70: peek(0x70),
        s7200: peek(0x7200), s7206: peek(0x7206),
      }
    };
    global.__FORGE_NES_STATE__ = state;
    return state;
  }
  global.ForgeNesReadState = readState;
})(typeof window !== "undefined" ? window : globalThis);
