import {test} from 'node:test';import assert from 'node:assert/strict';
import {start,callback,poll,challenge,handle,catalog} from '../broker.mjs';
class Bucket {
 constructor(){this.items=new Map();this.seq=0;}
 async get(key){const x=this.items.get(key);return x?{etag:x.etag,text:async()=>x.value}:null;}
 async put(key,value,options={}){const old=this.items.get(key);if(options.onlyIf&&old?.etag!==options.onlyIf.etagMatches)return null;const x={value,etag:String(++this.seq),metadata:options.customMetadata};this.items.set(key,x);return {etag:x.etag};}
 async list(){return {objects:[...this.items].map(([key,x])=>({key,customMetadata:x.metadata})),truncated:false};}
 async delete(keys){for(const k of Array.isArray(keys)?keys:[keys])this.items.delete(k);}
}
const setup=()=>({BUCKET:new Bucket(),MUSAB_PUBLIC_URL:'https://connect.example',MUSAB_GRANT_KEY:Buffer.alloc(32,9).toString('base64url'),GITHUB_CLIENT_ID:'test-client',GITHUB_CLIENT_SECRET:'test-secret'});
const verifier='a'.repeat(64);
const issue=async env=>start(env,'github',await challenge(verifier));
const fetcher=async()=>new Response(JSON.stringify({access_token:'account-test-token',refresh_token:'refresh-test-token',scope:'repo'}));
test('catalog includes only configured registrations',()=>{assert.equal(catalog(setup()).github,true);assert.equal(catalog(setup()).gmail,false);});
test('encrypted state persists across broker instances and contains no token plaintext',async()=>{const env=setup(),s=await issue(env);await callback({...env},'github',{state:s.id,code:'test-code'},fetcher);const raw=[...env.BUCKET.items.values()][0].value;assert(!raw.includes('account-test-token'));const out=await poll({...env},s.id,verifier);assert.equal(out.tokens.access_token,'account-test-token');await assert.rejects(poll(env,s.id,verifier));});
test('wrong handoff verifier cannot receive tokens',async()=>{const env=setup(),s=await issue(env);await callback(env,'github',{state:s.id,code:'test'},fetcher);await assert.rejects(poll(env,s.id,'b'.repeat(64)));assert.equal((await poll(env,s.id,verifier)).status,'ready');});
test('callback provider mismatch makes no exchange',async()=>{const env=setup(),s=await issue(env);let calls=0;await assert.rejects(callback(env,'gmail',{state:s.id,code:'test'},async()=>{calls++;return fetcher();}));assert.equal(calls,0);});
test('concurrent callbacks exchange only once',async()=>{const env=setup(),s=await issue(env);let calls=0;const f=async()=>{calls++;return fetcher();};const r=await Promise.allSettled([callback(env,'github',{state:s.id,code:'test'},f),callback(env,'github',{state:s.id,code:'test'},f)]);assert.equal(calls,1);assert.equal(r.filter(x=>x.status==='fulfilled').length,1);});
test('concurrent pollers receive credentials only once',async()=>{const env=setup(),s=await issue(env);await callback(env,'github',{state:s.id,code:'test'},fetcher);const r=await Promise.allSettled([poll(env,s.id,verifier),poll(env,s.id,verifier)]);assert.equal(r.filter(x=>x.status==='fulfilled').length,1);});
test('declined consent delivers a failed status without tokens',async()=>{const env=setup(),s=await issue(env);await assert.rejects(callback(env,'github',{state:s.id,error:'denied'},fetcher));const out=await poll(env,s.id,verifier);assert.equal(out.status,'failed');assert(!out.tokens);});
test('expired grants reject callbacks and polling',async()=>{const env=setup(),s=await issue(env),now=Date.now;Date.now=()=>now()+601000;try{await assert.rejects(callback(env,'github',{state:s.id,code:'test'},fetcher));await assert.rejects(poll(env,s.id,verifier));}finally{Date.now=now;}});
test('tampering with encrypted state fails closed',async()=>{const env=setup(),s=await issue(env);const x=env.BUCKET.items.get('oauth/'+s.id);x.value=x.value.replace(/"data":"./,'"data":"!');await assert.rejects(poll(env,s.id,verifier));});
test('callback HTML and errors never contain tokens or provider errors',async()=>{const env=setup(),s=await issue(env);const r=await handle(new Request(`https://connect.example/oauth/callback/github?state=${s.id}&code=test`),env,fetcher);const html=await r.text();assert.equal(r.status,200);assert(html.includes('musabai://connectors'));assert(!html.includes('account-test-token'));assert.equal(r.headers.get('cache-control'),'no-store');});
