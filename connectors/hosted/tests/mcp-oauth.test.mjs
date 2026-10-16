import {test} from 'node:test';
import assert from 'node:assert/strict';
import {catalog,start,exchange,challenge} from '../broker.mjs';
const registrations = [
 ['gitlabmcp','GITLAB_MCP','https://gitlab.com/oauth/token','https://gitlab.com/api/v4/mcp',false],
 ['notionmcp','NOTION_MCP','https://mcp.notion.com/token','https://mcp.notion.com',false],
 ['netlify','NETLIFY_MCP','https://mcp.netlify.com/oauth-server/token','https://mcp.netlify.com/mcp',false],
 ['miro','MIRO_MCP','https://mcp.miro.com/token','https://mcp.miro.com/',true],
 ['huggingface','HF_MCP','https://huggingface.co/oauth/token','https://huggingface.co/mcp',true]
];
for (const [id,prefix,endpoint,resource,secret] of registrations) {
 test(id+' uses the registered OAuth client with PKCE and bound resource',async()=>{
  const env={MUSAB_PUBLIC_URL:'https://connect.example',MUSAB_GRANT_KEY:Buffer.alloc(32,5).toString('base64url'),
   [prefix+'_CLIENT_ID']:'official-client',...(secret?{[prefix+'_CLIENT_SECRET']:'server-only-secret'}:{}),
   BUCKET:{list:async()=>({objects:[]}),put:async()=>({etag:'one'})}};
  assert.equal(catalog(env)[id],true);
  const out=await start(env,id,await challenge('x'.repeat(64)));
  const url=new URL(out.authorization_url);
  assert.equal(url.searchParams.get('resource'),resource);
  assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  assert.equal(url.searchParams.get('redirect_uri'),'https://connect.example/oauth/callback/'+id);
  assert.equal(url.searchParams.get('client_secret'),null);
  for(const grant of [
    {grant_type:'authorization_code',code:'user-code',code_verifier:'proof'},
    {grant_type:'refresh_token',refresh_token:'refresh-token'}
  ]) {
   let request;
   const token=await exchange(env,id,grant,async(url,options)=>{
    request=options;assert.equal(url,endpoint);
    return new Response(JSON.stringify({access_token:'account-token',refresh_token:'rotated',expires_in:3600}));
   });
   const form=new URLSearchParams(request.body);
   assert.equal(form.get('client_id'),'official-client');
   assert.equal(form.has('client_secret'),secret);
   assert.equal(form.get('resource'),resource);
   assert.equal(request.redirect,'manual');
   assert.equal(token.refresh_token,'rotated');
  }
  delete env[prefix+'_CLIENT_ID'];assert.equal(catalog(env)[id],false);
 });
}
test('a redirect never receives client credentials or tokens',async()=>{
 const env={HF_MCP_CLIENT_ID:'client',HF_MCP_CLIENT_SECRET:'secret'};
 let calls=0;
 await assert.rejects(exchange(env,'huggingface',{grant_type:'refresh_token',refresh_token:'private'},async()=>{
  calls++;return new Response('',{status:302,headers:{Location:'https://other.example/'}});
 }));
 assert.equal(calls,1);
});
