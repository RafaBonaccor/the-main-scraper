"""Create a local, interactive nearest-image and sale-performance report."""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import statistics
import struct
from contextlib import closing
from pathlib import Path


REPORT_TEMPLATE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vinted · Similarità immagini</title>
<style>
:root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#eef1f8;background:#10131a;font-synthesis:none}
*{box-sizing:border-box}body{margin:0;padding:28px;max-width:1500px;margin-inline:auto}h1{margin:0 0 8px;font-size:28px}p{color:#a9b1c3;margin:6px 0 20px}.muted{color:#8e98ad;font-size:13px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:22px 0}.stat,.panel{background:#191e29;border:1px solid #2b3343;border-radius:14px;padding:16px}.stat b{display:block;font-size:24px;margin-top:5px}.controls{display:grid;grid-template-columns:minmax(240px,1fr) 190px;gap:12px;margin:18px 0}.controls input,.controls select{width:100%;padding:12px;border:1px solid #39445a;background:#111722;color:#eff3fb;border-radius:9px;font:inherit}
.layout{display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:16px;align-items:start}.candidate-list{display:grid;gap:7px;max-height:70vh;overflow:auto}.candidate{display:flex;gap:10px;align-items:center;text-align:left;background:#121721;color:inherit;border:1px solid #293142;border-radius:10px;padding:8px;cursor:pointer}.candidate:hover,.candidate.active{border-color:#88a7ff;background:#202a3c}.candidate img,.result img{width:66px;height:66px;object-fit:cover;border-radius:7px;background:#252b37}.candidate span{min-width:0}.candidate strong,.result strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.candidate small,.result small{display:block;color:#a9b1c3;margin-top:4px}.results{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;margin-top:14px}.result{background:#121721;border:1px solid #293142;border-radius:12px;padding:11px}.result img{width:100%;height:160px}.result a{color:#c2d1ff;text-decoration:none}.badge{display:inline-block;border-radius:999px;padding:3px 8px;font-size:11px;margin:8px 4px 0 0;background:#283247;color:#dce6ff}.badge.sold{background:#164737;color:#bbf1d9}.badge.active{background:#55421d;color:#ffe1a0}.empty{color:#a9b1c3;padding:24px}.score{color:#9bb4ff;font-variant-numeric:tabular-nums}.bar{height:7px;background:#2b3343;border-radius:9px;overflow:hidden;margin-top:9px}.bar i{display:block;height:100%;background:#88a7ff}
.mode-switch{display:flex;gap:8px;margin:20px 0}.mode-switch button,.game-actions button,.game-setup button{border:1px solid #39445a;background:#171d29;color:#eaf0fc;border-radius:10px;padding:10px 15px;font:inherit;font-weight:650;cursor:pointer}.mode-switch button.active,.game-actions .yes{background:#174a37;border-color:#2c9269}.game-actions .no{background:#572a31;border-color:#b25360}.game-setup{display:flex;flex-wrap:wrap;align-items:end;gap:10px;margin:12px 0}.game-setup label{display:grid;gap:6px;color:#a9b1c3;font-size:13px}.game-setup select{padding:10px;border:1px solid #39445a;background:#111722;color:#eff3fb;border-radius:9px;font:inherit}.game-setup button.primary{background:#405fb9;border-color:#7594ee}.game-progress{display:flex;justify-content:space-between;gap:12px;align-items:center;margin:16px 0 8px}.game-progress .bar{flex:1}.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}.pair-card{min-width:0;background:#121721;border:1px solid #293142;border-radius:14px;padding:14px}.pair-card img{display:block;width:100%;height:min(42vh,380px);object-fit:contain;background:#0c1017;border-radius:9px}.pair-card h3{margin:12px 0 5px}.pair-card p{margin:0;color:#a9b1c3;font-size:14px}.game-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:15px}.game-actions button{min-width:140px}.game-note{color:#a9b1c3;font-size:13px}.game-summary{font-size:18px}.hidden{display:none!important}
@media(max-width:760px){body{padding:16px}.layout{grid-template-columns:1fr}.candidate-list{max-height:260px}.controls{grid-template-columns:1fr}}
@media(max-width:620px){.pair{grid-template-columns:1fr}.pair-card img{height:34vh}.game-actions button{flex:1}}
</style></head><body>
<h1>Similarità visiva e velocità di vendita</h1><p id="model"></p>
<section class="stats" id="stats"></section>
<nav class="mode-switch"><button id="explore-tab" class="active">Esplora report</button><button id="game-tab">Gioco: valuta i vicini</button></nav>
<main id="explore-view">
<div class="controls"><input id="search" type="search" placeholder="Cerca un annuncio per titolo o ID…"><select id="filter"><option value="all">Tutti gli annunci</option><option value="sold">Venduti con durata nota</option><option value="0-6">Venduti in 0–6 ore</option><option value="6-12">Venduti in 6–12 ore</option><option value="12-24">Venduti in 12–24 ore</option><option value="24+">Venduti oltre 24 ore</option><option value="active">Attivi (censurati)</option></select></div>
<div class="layout"><section class="panel"><h2>Immagini indicizzate</h2><div class="muted" id="candidate-count"></div><div class="candidate-list" id="candidates"></div></section>
<section class="panel"><div id="selected"><div class="empty">Cerca o seleziona un annuncio per trovare le immagini visivamente più simili.</div></div><div class="results" id="results"></div></section></div></main>
<main id="game-view" class="hidden"><section class="panel"><h2>Valida i vicini trovati da CLIP</h2><p>Guarda le due immagini e valuta se mostrano la stessa tipologia di oggetto. Ignora sfondo, luce e inquadratura: conta l’oggetto.</p><div class="game-setup"><label>Numero coppie<select id="game-size"><option value="25">25 · veloce</option><option value="50" selected>50 · consigliato</option><option value="100">100 · approfondito</option></select></label><button id="game-start" class="primary">Avvia / nuova sessione</button><button id="game-export">Esporta valutazioni JSON</button><button id="game-undo">Annulla ultima</button></div><div class="game-note">Salvataggio automatico in questo browser. Esporta periodicamente il JSON per conservarne una copia.</div><div class="game-progress"><strong id="game-counter">Sessione non avviata</strong><div class="bar"><i id="game-bar" style="width:0%"></i></div><span id="game-counts" class="muted"></span></div><div id="game-pair" class="pair"></div><div id="game-result" class="game-summary"></div><div class="game-actions"><button id="game-yes" class="yes">← Simili (A)</button><button id="game-no" class="no">Diversi (D) →</button><button id="game-skip">Non so / salta (S)</button></div></section></main>
<script id="catalog-data" type="application/json">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('catalog-data').textContent),items=data.items;
document.getElementById('model').textContent=`Modello: ${data.model} · revisione ${data.revision} · preprocessing ${data.preprocessing} · generato ${data.generated_at}`;
const asHours=s=>s==null?'n/d':(s/3600).toFixed(1)+' h';
const statRows=[['Immagini uniche',items.length],['Annunci distinti',data.stats.total],['Venduti con durata',data.stats.sold_with_duration],['Attivi / censurati',data.stats.active],['Mediana vendita',asHours(data.stats.median_seconds)],['0–6 ore',data.stats.bins['0-6']],['6–12 ore',data.stats.bins['6-12']],['12–24 ore',data.stats.bins['12-24']],['Oltre 24 ore',data.stats.bins['24+']]];
const stats=document.getElementById('stats');for(const [label,value] of statRows){const card=document.createElement('div');card.className='stat';card.textContent=label;const b=document.createElement('b');b.textContent=value;card.appendChild(b);stats.appendChild(card)}
const search=document.getElementById('search'),filter=document.getElementById('filter'),candidates=document.getElementById('candidates'),results=document.getElementById('results'),selected=document.getElementById('selected');let current=null;
function matchesFilter(x){const d=x.duration;if(filter.value==='all')return true;if(filter.value==='active')return x.status==='active';if(filter.value==='sold')return x.status==='sold'&&d!=null;if(x.status!=='sold'||d==null)return false;const h=d/3600;if(filter.value==='0-6')return h<6;if(filter.value==='6-12')return h>=6&&h<12;if(filter.value==='12-24')return h>=12&&h<=24;return h>24}
function cardText(x){return `${x.title||'Annuncio senza titolo'} · ${x.item_id||'ID n/d'} · ${x.status==='sold'?'venduto':'attivo'} · ${asHours(x.duration)}`}
function renderCandidates(){const q=search.value.trim().toLowerCase();const visible=items.filter(x=>matchesFilter(x)&&(!q||`${x.title} ${x.item_id} ${x.seller}`.toLowerCase().includes(q))).slice(0,250);document.getElementById('candidate-count').textContent=`${visible.length} mostrati (massimo 250)`;candidates.replaceChildren();for(const x of visible){const b=document.createElement('button');b.className='candidate'+(current?.sha===x.sha?' active':'');const img=document.createElement('img');img.loading='lazy';img.src=x.thumb||'';img.alt='';const span=document.createElement('span');const strong=document.createElement('strong');strong.textContent=x.title||'Annuncio senza titolo';const small=document.createElement('small');small.textContent=`${x.item_id||'ID n/d'} · ${x.status==='sold'?'venduto':'attivo'} · ${asHours(x.duration)}`;span.append(strong,small);b.append(img,span);b.onclick=()=>selectItem(x);candidates.appendChild(b)}}
const vectorCache=new Map();function decode(x){if(vectorCache.has(x.sha))return vectorCache.get(x.sha);const raw=atob(x.vector);const view=new DataView(Uint8Array.from(raw,c=>c.charCodeAt(0)).buffer);const out=new Float32Array(view.byteLength/4);for(let i=0;i<out.length;i++)out[i]=view.getFloat32(i*4,true);vectorCache.set(x.sha,out);return out}
function selectItem(x){current=x;document.querySelectorAll('.candidate').forEach(b=>b.classList.remove('active'));renderCandidates();selected.replaceChildren();const h=document.createElement('h2');h.textContent=`Più simili a: ${x.title||x.item_id}`;const p=document.createElement('p');p.className='muted';p.textContent=cardText(x);if(x.link){const a=document.createElement('a');a.href=x.link;a.target='_blank';a.rel='noreferrer';a.textContent='Apri annuncio';p.append(document.createTextNode(' · '),a)}selected.append(h,p);const q=decode(x);const nearest=items.filter(y=>y.sha!==x.sha&&matchesFilter(y)).map(y=>{const v=decode(y);let score=0;for(let i=0;i<Math.min(q.length,v.length);i++)score+=q[i]*v[i];return {item:y,score}}).sort((a,b)=>b.score-a.score).slice(0,12);results.replaceChildren();if(!nearest.length){results.innerHTML='<div class="empty">Nessun vicino per questo filtro.</div>';return}for(const row of nearest){const y=row.item,article=document.createElement('article');article.className='result';const img=document.createElement('img');img.loading='lazy';img.src=y.thumb||'';img.alt='';const title=document.createElement('strong');title.textContent=y.title||'Annuncio senza titolo';article.append(img,title);const small=document.createElement('small');small.textContent=`${y.item_id||'ID n/d'} · ${y.price==null?'prezzo n/d':y.price.toFixed(2)+' €'} · ${y.favorites==null?'like n/d':y.favorites+' like'}`;article.append(small);const score=document.createElement('span');score.className='badge score';score.textContent=`similarità ${(row.score*100).toFixed(1)}%`;const state=document.createElement('span');state.className='badge '+(y.status==='sold'?'sold':'active');state.textContent=y.status==='sold'?`venduto · ${asHours(y.duration)}`:'attivo · durata censurata';article.append(score,state);if(y.link){const a=document.createElement('a');a.href=y.link;a.target='_blank';a.rel='noreferrer';a.textContent='Apri annuncio';article.append(document.createElement('br'),a)}results.appendChild(article)}}
search.addEventListener('input',renderCandidates);filter.addEventListener('change',()=>{renderCandidates();if(current)selectItem(current)});renderCandidates();

const gameStorageKey=`vinted-clip-validation:${data.model}:${data.revision}:${data.preprocessing}`;
const bySha=new Map(items.map(item=>[item.sha,item]));
function readGameStorage(key,fallback){try{const value=localStorage.getItem(key);return value?JSON.parse(value):fallback}catch{return fallback}}
let gameRatings=readGameStorage(gameStorageKey,{}),gameSession=readGameStorage(gameStorageKey+':session',null);
function saveGame(){try{localStorage.setItem(gameStorageKey,JSON.stringify(gameRatings));localStorage.setItem(gameStorageKey+':session',JSON.stringify(gameSession))}catch(error){document.getElementById('game-result').textContent='Impossibile salvare nel browser: esporta subito il JSON. '+error.message}}
function pairKey(a,b){return [a,b].sort().join(':')}
function buildGameQueue(count){
  const anchors=items.filter(x=>x.thumb&&x.link);
  for(let i=anchors.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[anchors[i],anchors[j]]=[anchors[j],anchors[i]]}
  const queue=[],queued=new Set();
  for(const anchor of anchors){
    if(queue.length>=count)break;
    const av=decode(anchor);let best=null;
    for(const candidate of items){
      if(candidate.sha===anchor.sha||!candidate.thumb||!candidate.link)continue;
      if(anchor.item_id&&candidate.item_id===anchor.item_id)continue;
      const key=pairKey(anchor.sha,candidate.sha);if(gameRatings[key]||queued.has(key))continue;
      const bv=decode(candidate);let score=0;for(let d=0;d<Math.min(av.length,bv.length);d++)score+=av[d]*bv[d];
      if(!best||score>best.score)best={anchor:anchor.sha,candidate:candidate.sha,score,key};
    }
    if(best){queue.push(best);queued.add(best.key)}
  }
  return queue;
}
const gamePair=document.getElementById('game-pair'),gameCounter=document.getElementById('game-counter'),gameCounts=document.getElementById('game-counts'),gameBar=document.getElementById('game-bar'),gameResult=document.getElementById('game-result'),yesButton=document.getElementById('game-yes'),noButton=document.getElementById('game-no'),skipButton=document.getElementById('game-skip');
function renderGame(){
  const rated=Object.values(gameRatings),similar=rated.filter(x=>x.label==='similar').length,different=rated.filter(x=>x.label==='different').length;
  gameCounts.textContent=`${similar} simili · ${different} diversi · ${rated.length} giudicati`;
  if(!gameSession){gameCounter.textContent='Sessione non avviata';gameBar.style.width='0%';gamePair.replaceChildren();gameResult.textContent='Premi “Avvia” per iniziare.';yesButton.disabled=noButton.disabled=skipButton.disabled=true;return}
  const total=gameSession.queue.length,cursor=gameSession.cursor;gameBar.style.width=(total?Math.min(100,cursor/total*100):100)+'%';
  gameCounter.textContent=total?`Coppia ${Math.min(cursor+1,total)} di ${total}`:'Sessione vuota';yesButton.disabled=noButton.disabled=skipButton.disabled=true;gamePair.replaceChildren();
  if(cursor>=total){gameResult.textContent=`Sessione completata. ${rated.length} valutazioni salvate (${similar} simili, ${different} diversi). Puoi esportarle o avviare un nuovo campione.`;return}
  const pair=gameSession.queue[cursor],a=bySha.get(pair.anchor),b=bySha.get(pair.candidate);
  if(!a||!b){gameSession.cursor++;saveGame();renderGame();return}
  gameResult.textContent='';
  for(const [item,label] of [[a,'Riferimento'],[b,'Vicino suggerito da CLIP']]){
    const card=document.createElement('article');card.className='pair-card';const heading=document.createElement('span');heading.className='badge';heading.textContent=label;
    const img=document.createElement('img');img.src=item.preview||item.thumb;img.alt='';const title=document.createElement('h3');title.textContent=item.title||'Annuncio senza titolo';
    const info=document.createElement('p');info.textContent=`${item.item_id||'ID n/d'} · ${item.status==='sold'?'venduto':'attivo'} · ${item.price==null?'prezzo n/d':item.price.toFixed(2)+' €'}`;
    card.append(heading,img,title,info);if(item.link){const link=document.createElement('a');link.href=item.link;link.target='_blank';link.rel='noreferrer';link.textContent='Apri annuncio';card.append(link)}gamePair.appendChild(card);
  }
  yesButton.disabled=noButton.disabled=skipButton.disabled=false;
}
function answerGame(label){
  if(!gameSession||gameSession.cursor>=gameSession.queue.length)return;
  const pair=gameSession.queue[gameSession.cursor];
  if(label!=='skip'){gameSession.history.push({kind:'vote',key:pair.key,previous:gameRatings[pair.key]||null});gameRatings[pair.key]={label,anchor:pair.anchor,candidate:pair.candidate,cosine:pair.score,model:data.model,revision:data.revision,preprocessing:data.preprocessing,judged_at:new Date().toISOString()}}
  else gameSession.history.push({kind:'skip'});
  gameSession.cursor++;saveGame();renderGame();
}
document.getElementById('explore-tab').onclick=()=>{document.getElementById('explore-view').classList.remove('hidden');document.getElementById('game-view').classList.add('hidden');document.getElementById('explore-tab').classList.add('active');document.getElementById('game-tab').classList.remove('active')};
document.getElementById('game-tab').onclick=()=>{document.getElementById('explore-view').classList.add('hidden');document.getElementById('game-view').classList.remove('hidden');document.getElementById('game-tab').classList.add('active');document.getElementById('explore-tab').classList.remove('active');renderGame()};
document.getElementById('game-start').onclick=()=>{if(gameSession&&gameSession.cursor<gameSession.queue.length&&!confirm('Vuoi abbandonare la sessione in corso e iniziare un nuovo campione? Le valutazioni già salvate resteranno.'))return;gameSession={queue:buildGameQueue(Number(document.getElementById('game-size').value)),cursor:0,history:[]};saveGame();renderGame()};
yesButton.onclick=()=>answerGame('similar');noButton.onclick=()=>answerGame('different');skipButton.onclick=()=>answerGame('skip');
document.getElementById('game-undo').onclick=()=>{if(!gameSession||gameSession.cursor===0)return;const previous=gameSession.history.pop();gameSession.cursor--;if(previous?.kind==='vote'){if(previous.previous)gameRatings[previous.key]=previous.previous;else delete gameRatings[previous.key]}saveGame();renderGame()};
document.getElementById('game-export').onclick=()=>{const payload={schema_version:1,model:data.model,revision:data.revision,preprocessing:data.preprocessing,exported_at:new Date().toISOString(),ratings:gameRatings};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`clip-neighbor-labels-${data.revision.slice(0,8)}.json`;link.click();URL.revokeObjectURL(url)};
document.getElementById('game-import').onchange=async event=>{const file=event.target.files?.[0];if(!file)return;try{const payload=JSON.parse(await file.text());if(payload.model!==data.model||payload.revision!==data.revision)throw new Error('Il backup appartiene a un altro modello o revisione.');gameRatings={...gameRatings,...payload.ratings};saveGame();renderGame();gameResult.textContent='Backup importato.'}catch(error){gameResult.textContent=`Import fallito: ${error.message}`}event.target.value=''};
document.addEventListener('keydown',event=>{if(document.getElementById('game-view').classList.contains('hidden')||event.target.matches('input,select,textarea'))return;if(event.key.toLowerCase()==='a')answerGame('similar');if(event.key.toLowerCase()==='d')answerGame('different');if(event.key.toLowerCase()==='s')answerGame('skip')});renderGame();
</script></body></html>"""


def _decode_vector(blob: bytes, dimension: int) -> list[float]:
    if len(blob) != dimension * 4:
        raise ValueError(f"Dimensione embedding incoerente: {len(blob)} bytes per {dimension} valori")
    return list(struct.unpack(f"<{dimension}f", blob))


def _thumbnail(image_path: str, image_sha256: str, thumbnail_dir: Path) -> str:
    try:
        from PIL import Image, ImageOps

        with Image.open(image_path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((192, 192))
            target = thumbnail_dir / f"{image_sha256}.jpg"
            image.save(target, "JPEG", quality=68, optimize=True)
        return f"thumbnails/{image_sha256}.jpg"
    except Exception:
        return ""


def _preview(image_path: str, image_sha256: str, preview_dir: Path) -> str:
    """Create a sharper local preview for human labeling; embeddings use the original archived file."""
    try:
        from PIL import Image, ImageOps

        with Image.open(image_path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1200, 1200))
            target = preview_dir / f"{image_sha256}.jpg"
            image.save(target, "JPEG", quality=90, optimize=True)
        return f"previews/{image_sha256}.jpg"
    except Exception:
        return ""


def create_report(
    store_db: str | Path,
    output_html: str | Path,
    *,
    model_name: str | None = None,
    model_revision: str | None = None,
    preprocessing_version: str | None = None,
) -> dict[str, object]:
    store_path = Path(store_db).expanduser().resolve()
    output_path = Path(output_html).expanduser().resolve()
    if not store_path.is_file():
        raise FileNotFoundError(f"Database embeddings non trovato: {store_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail_dir = output_path.parent / "thumbnails"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_path.parent / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(store_path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        versions = connection.execute(
            """SELECT model_name,model_revision,preprocessing_version,MAX(created_at) AS latest
               FROM image_embeddings GROUP BY model_name,model_revision,preprocessing_version
               ORDER BY latest DESC"""
        ).fetchall()
        if model_name:
            versions = [row for row in versions if row["model_name"] == model_name]
        if model_revision:
            versions = [row for row in versions if row["model_revision"] == model_revision]
        if preprocessing_version:
            versions = [row for row in versions if row["preprocessing_version"] == preprocessing_version]
        if not versions:
            raise ValueError("Nessun embedding corrisponde al modello/revisione richiesti.")
        version = versions[0]
        embeddings = connection.execute(
            """SELECT e.image_sha256,e.embedding_dim,e.vector_blob,a.original_path
               FROM image_embeddings e JOIN image_assets a USING(image_sha256)
               WHERE e.model_name=? AND e.model_revision=? AND e.preprocessing_version=?
               ORDER BY e.created_at DESC""",
            (version["model_name"], version["model_revision"], version["preprocessing_version"]),
        ).fetchall()
        locations = connection.execute(
            "SELECT image_sha256,image_path FROM image_asset_locations ORDER BY first_seen_at"
        ).fetchall()
        observations = connection.execute(
            """SELECT image_sha256,item_id,item_link,image_url,title,status,first_seen_at,sold_at,
                      sale_duration_seconds,sale_duration_source,price_value,favorite_count,seller_member_id,indexed_at
               FROM listing_image_observations ORDER BY indexed_at DESC"""
        ).fetchall()

    by_sha: dict[str, list[sqlite3.Row]] = {}
    for observation in observations:
        by_sha.setdefault(str(observation["image_sha256"]), []).append(observation)
    paths_by_sha: dict[str, list[str]] = {}
    for location in locations:
        paths_by_sha.setdefault(str(location["image_sha256"]), []).append(str(location["image_path"]))
    latest_by_listing: dict[str, sqlite3.Row] = {}
    for observation in observations:
        key = str(observation["item_id"] or observation["item_link"] or "")
        if key and key not in latest_by_listing:
            latest_by_listing[key] = observation
    latest_observations = list(latest_by_listing.values())
    sale_durations = [
        int(row["sale_duration_seconds"])
        for row in latest_observations
        if row["status"] == "sold" and row["sale_duration_seconds"] is not None
    ]
    bins = {
        "0-6": sum(value < 6 * 3600 for value in sale_durations),
        "6-12": sum(6 * 3600 <= value < 12 * 3600 for value in sale_durations),
        "12-24": sum(12 * 3600 <= value <= 24 * 3600 for value in sale_durations),
        "24+": sum(value > 24 * 3600 for value in sale_durations),
    }
    performance_stats = {
        "total": len(latest_observations),
        "active": sum(row["status"] == "active" for row in latest_observations),
        "sold_with_duration": len(sale_durations),
        "median_seconds": int(statistics.median(sale_durations)) if sale_durations else None,
        "bins": bins,
    }
    items: list[dict[str, object]] = []
    for row in embeddings:
        sha = str(row["image_sha256"])
        related = by_sha.get(sha, [])
        representative = next((o for o in related if o["status"] == "sold" and o["sale_duration_seconds"] is not None), None)
        if representative is None:
            representative = next((o for o in related if o["status"] == "sold"), None)
        if representative is None and related:
            representative = related[0]
        observation = representative
        dimension = int(row["embedding_dim"])
        vector = bytes(row["vector_blob"])
        _decode_vector(vector, dimension)
        image_path = str(row["original_path"])
        if not Path(image_path).is_file():
            image_path = next((path for path in paths_by_sha.get(sha, []) if Path(path).is_file()), image_path)
        item = {
            "sha": sha,
            "vector": base64.b64encode(vector).decode("ascii"),
            "thumb": _thumbnail(image_path, sha, thumbnail_dir),
            "preview": _preview(image_path, sha, preview_dir),
            "title": str(observation["title"] or "") if observation else "",
            "item_id": str(observation["item_id"] or "") if observation else "",
            "link": str(observation["item_link"] or "") if observation else "",
            "image_url": str(observation["image_url"] or "") if observation else "",
            "status": str(observation["status"] or "unknown") if observation else "unknown",
            "duration": int(observation["sale_duration_seconds"]) if observation and observation["sale_duration_seconds"] is not None else None,
            "duration_source": str(observation["sale_duration_source"] or "") if observation else "",
            "price": float(observation["price_value"]) if observation and observation["price_value"] is not None else None,
            "favorites": int(observation["favorite_count"]) if observation and observation["favorite_count"] is not None else None,
            "seller": str(observation["seller_member_id"] or "") if observation else "",
            "linked_listings": len(related),
        }
        items.append(item)

    if not items:
        raise ValueError("Il modello selezionato non contiene vettori da mostrare.")
    payload = {
        "model": str(version["model_name"]),
        "revision": str(version["model_revision"]),
        "preprocessing": str(version["preprocessing_version"]),
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "observation_count": len(observations),
        "stats": performance_stats,
        "items": items,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    output_path.write_text(REPORT_TEMPLATE.replace("__DATA__", encoded), encoding="utf-8")
    return {
        "ok": True,
        "report_path": str(output_path),
        "items": len(items),
        "observations": len(observations),
        "model": payload["model"],
        "revision": payload["revision"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local interactive Vinted image similarity report.")
    parser.add_argument("--store-db", default="data/vinted_image_embeddings.db")
    parser.add_argument("--output", default="output/vinted_image_embeddings/index.html")
    parser.add_argument("--model", default="")
    parser.add_argument("--revision", default="")
    parser.add_argument("--preprocessing", default="")
    args = parser.parse_args(argv)
    try:
        result = create_report(
            args.store_db,
            args.output,
            model_name=args.model or None,
            model_revision=args.revision or None,
            preprocessing_version=args.preprocessing or None,
        )
    except Exception as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
