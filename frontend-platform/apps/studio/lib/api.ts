export type Session = {token:string; csrf:string};
export type Workspace = {workspace_id:string; name:string};
export type Bot = {bot_id:string; display_name?:string; username?:string; telegram_bot_id?:number; connection_state?:string; archived?:boolean; description?:string; category?:string; tags?:string[]};
export type Identity = {user:{display_name:string; is_platform_admin:boolean; csrf_token?:string; user_id?:number}; workspaces:Workspace[]; catalogs:{workspace_roles:string[]; permission_scopes:string[]}};
export type WorkspaceDetail = {workspace:{name:string}; my_role:string; my_permissions:Record<string,boolean>};
export class ApiError extends Error { constructor(message:string,public status:number){super(message)} }
export async function request(path:string, session:Session | null, options:{method?:string; body?:unknown; signal?:AbortSignal}={}):Promise<unknown> {
  if (!path.startsWith('/api/v1/') || path.includes('\\')) throw new TypeError('Only same-origin API paths are supported.');
  const url = new URL(path,window.location.origin);
  if(url.origin!==window.location.origin || !url.pathname.startsWith('/api/v1/')) throw new TypeError('Only same-origin API paths are supported.');
  const method=(options.method||'GET').toUpperCase(), headers:Record<string,string>={};
  if(session?.token) headers.Authorization=`Bearer ${session.token}`;
  if(session?.csrf && !['GET','HEAD','OPTIONS'].includes(method)) headers['X-CSRF-Token']=session.csrf;
  const form=options.body instanceof FormData;
  if(options.body!==undefined&&!form) headers['Content-Type']='application/json';
  const response=await fetch(path,{method,headers,body:options.body===undefined?undefined:form?options.body as FormData:JSON.stringify(options.body),signal:options.signal,cache:'no-store'});
  const payload:unknown=(response.headers.get('Content-Type')||'').includes('application/json') ? await response.json() : await response.text();
  if(!response.ok){const message=typeof payload==='object'&&payload!==null&&'error' in payload&&typeof payload.error==='string'?payload.error:`HTTP ${response.status}`;throw new ApiError(message,response.status)}
  return payload;
}
const object = (value:unknown): value is Record<string,unknown> => typeof value==='object' && value!==null && !Array.isArray(value);
export function identity(value:unknown):Identity {
  if(!object(value)||!object(value.user)||typeof value.user.display_name!=='string'||typeof value.user.is_platform_admin!=='boolean'||!Array.isArray(value.workspaces)) throw new Error('The server returned an invalid account response.');
  const workspaces=value.workspaces.map(w=>{if(!object(w)||typeof w.workspace_id!=='string'||typeof w.name!=='string')throw new Error('The server returned an invalid workspace.');return {workspace_id:w.workspace_id,name:w.name}});
  const catalogs=object(value.catalogs)?value.catalogs:{}; const strings=(v:unknown)=>Array.isArray(v)?v.filter((x):x is string=>typeof x==='string'):[];
  return {user:{display_name:value.user.display_name,is_platform_admin:value.user.is_platform_admin,csrf_token:typeof value.user.csrf_token==='string'?value.user.csrf_token:undefined,user_id:typeof value.user.user_id==='number'&&Number.isSafeInteger(value.user.user_id)&&value.user.user_id>0?value.user.user_id:undefined},workspaces,catalogs:{workspace_roles:strings(catalogs.workspace_roles),permission_scopes:strings(catalogs.permission_scopes)}};
}
export function login(value:unknown):Session {
  if(!object(value)||typeof value.session_token!=='string'||typeof value.csrf_token!=='string')throw new Error('The server did not return a valid session.');
  return {token:value.session_token,csrf:value.csrf_token};
}
export function workspaceDetail(value:unknown):WorkspaceDetail {
  if(!object(value)||!object(value.workspace)||typeof value.workspace.name!=='string'||typeof value.my_role!=='string'||!object(value.my_permissions))throw new Error('The server returned invalid workspace details.');
  const permissions:Record<string,boolean>={}; for(const [key,val] of Object.entries(value.my_permissions)) if(typeof val==='boolean') permissions[key]=val;
  return {workspace:{name:value.workspace.name},my_role:value.my_role,my_permissions:permissions};
}
export function botList(value:unknown):Bot[] {
  if(!object(value)||!Array.isArray(value.bots))throw new Error('The server returned an invalid bot list.');
  return value.bots.map(bot=>{if(!object(bot)||typeof bot.bot_id!=='string')throw new Error('The server returned an invalid bot record.');return {bot_id:bot.bot_id,display_name:typeof bot.display_name==='string'?bot.display_name:undefined,username:typeof bot.username==='string'?bot.username:undefined,telegram_bot_id:typeof bot.telegram_bot_id==='number'?bot.telegram_bot_id:undefined,connection_state:typeof bot.connection_state==='string'?bot.connection_state:undefined,archived:bot.archived===true,description:typeof bot.description==='string'?bot.description:undefined,category:typeof bot.category==='string'?bot.category:undefined,tags:Array.isArray(bot.tags)?bot.tags.filter((t):t is string=>typeof t==='string'):[]};});
}

export function createdWorkspaceId(value:unknown):string {
  if (!object(value) || !object(value.workspace) || typeof value.workspace.workspace_id !== 'string' || !value.workspace.workspace_id) throw new Error('The server did not return a valid workspace identifier.');
  return value.workspace.workspace_id;
}

/** Extract a Telegram one-time key; a pasted URL is never visited or sent intact. */
export function oneTimeKey(input:string):string | null {
  let key = input.trim();
  if (key.startsWith('https://') || key.startsWith('http://')) {
    try {
      const url = new URL(key);
      if (url.protocol !== 'https:' && url.origin !== window.location.origin) return null;
      if (!['/app','/next-preview','/next-preview/'].includes(url.pathname)) return null;
      key = url.hash.slice(1);
    } catch { return null; }
  }
  if (key.startsWith('#')) key = key.slice(1);
  return /^l\.[A-Za-z0-9_-]+$/.test(key) ? key : null;
}

export type CommandRecord = {command:string;description:string;category:string;permission:string;visibility:string;enabled:boolean;handler_bound:boolean;custom_reply_text:string};
export function commandList(value:unknown):CommandRecord[] {
  if (!object(value) || !Array.isArray(value.commands)) throw new Error('The server returned an invalid command list.');
  return value.commands.map(c => {
    if (!object(c) || typeof c.command !== 'string' || typeof c.description !== 'string' || typeof c.enabled !== 'boolean') throw new Error('The server returned an invalid command record.');
    return {command:c.command,description:c.description,category:typeof c.category==='string'?c.category:'',permission:typeof c.permission==='string'?c.permission:'',visibility:typeof c.visibility==='string'?c.visibility:'',enabled:c.enabled,handler_bound:c.handler_bound===true,custom_reply_text:typeof c.custom_reply_text==='string'?c.custom_reply_text:''};
  });
}

export type FileRecord = {file_uid:string;filename:string;size_bytes:number;category:string;deleted:boolean};
export type FileResults = {items:FileRecord[];total:number;total_bytes:number;page:number;pages:number;sample_limit:number;sample_truncated:boolean};
export function fileResults(value:unknown):FileResults {
  if(!object(value)||!Array.isArray(value.items)||typeof value.total!=='number'||typeof value.total_bytes!=='number'||typeof value.page!=='number'||typeof value.pages!=='number'||typeof value.sample_limit!=='number'||typeof value.sample_truncated!=='boolean')throw new Error('The server returned an invalid file list.');
  return {total:value.total,total_bytes:value.total_bytes,page:value.page,pages:value.pages,sample_limit:value.sample_limit,sample_truncated:value.sample_truncated,items:value.items.map(f=>{if(!object(f)||typeof f.file_uid!=='string'||typeof f.filename!=='string')throw new Error('The server returned an invalid file record.');return {file_uid:f.file_uid,filename:f.filename,size_bytes:typeof f.size_bytes==='number'?f.size_bytes:0,category:typeof f.category==='string'?f.category:'other',deleted:f.deleted===true}})};
}

export type TemplateRecord = {id:string;name:string;category:string;description:string;version:string;payload:Record<string,unknown>|null;origin:'official'|'workspace'|'shared'};
export function templateList(value:unknown,workspace:string):TemplateRecord[] {
  if(!object(value)||!Array.isArray(value.official)||!Array.isArray(value.workspace_templates))throw new Error('The server returned an invalid template list.');
  const parse=(entry:unknown,official:boolean):TemplateRecord=>{
    if(!object(entry)||typeof entry.template_id!=='string'||typeof entry.name!=='string'||typeof entry.category!=='string')throw new Error('The server returned an invalid template record.');
    return {id:entry.template_id,name:entry.name,category:entry.category,description:typeof entry.description==='string'?entry.description:'',version:typeof entry.version==='string'?entry.version:'',payload:object(entry.payload)?entry.payload:null,origin:official?'official':entry.workspace_id===workspace?'workspace':'shared'};
  };
  return [...value.official.map(v=>parse(v,true)),...value.workspace_templates.map(v=>parse(v,false))];
}
export type NotificationRecord = {id:string;title:string;message:string;category:string;severity:string;created_at:string;read:boolean};
export function notificationList(value:unknown):NotificationRecord[] {
  if(!object(value)||!Array.isArray(value.notifications))throw new Error('The server returned an invalid notification list.');
  return value.notifications.map(n=>{
    if(!object(n)||typeof n.notification_id!=='string'||!n.notification_id)throw new Error('The server returned an invalid notification record.');
    return {id:n.notification_id,title:typeof n.title==='string'?n.title:'Notification',message:typeof n.message==='string'?n.message:'',category:typeof n.category==='string'?n.category:'other',severity:typeof n.severity==='string'?n.severity:'other',created_at:typeof n.created_at==='string'?n.created_at:'',read:n.read===true};
  });
}
export type FleetSummary={total:number|null;running:number|null;bots:Bot[]};
export function fleetSummary(value:unknown):FleetSummary {
  if(!object(value))throw new Error('The server returned an invalid fleet summary.');
  const count=(n:unknown)=>typeof n==='number'&&Number.isSafeInteger(n)&&n>=0?n:null;
  const counts=object(value.state_counts)?value.state_counts:{};
  return {total:count(value.total_bots),running:count(counts.running),bots:botList({bots:value.bots})};
}

export type TeamMember={user_id:number;role:string;permissions:Record<string,boolean>};
export type TeamData={members:TeamMember[];role_defaults:Record<string,Record<string,boolean>>;reviewed_membership_guards:boolean};
export function teamData(value:unknown):TeamData {
  if(!object(value)||!Array.isArray(value.members))throw new Error('The server returned an invalid team list.');
  const members=value.members.map(m=>{if(!object(m)||typeof m.user_id!=='number'||!Number.isSafeInteger(m.user_id)||m.user_id<=0||typeof m.role!=='string'||!object(m.permissions)||Object.values(m.permissions).some(v=>typeof v!=='boolean'))throw new Error('The server returned an invalid membership record.');return {user_id:m.user_id,role:m.role,permissions:m.permissions as Record<string,boolean>}});
  const role_defaults:Record<string,Record<string,boolean>>={};if(object(value.role_defaults))for(const [role,permissions] of Object.entries(value.role_defaults)){if(object(permissions)&&Object.values(permissions).every(v=>typeof v==='boolean'))role_defaults[role]=permissions as Record<string,boolean>}
  return {members,role_defaults,reviewed_membership_guards:value.reviewed_membership_guards===true};
}

export type AnalyticsData={storedFiles:number;storageBytes:number;periodUploads:number;filesDelta:number;sampled:boolean;days:string[];uploads:number[];bytesAdded:number[]};
export function analyticsData(value:unknown):AnalyticsData {
 if(!object(value)||!object(value.totals)||!object(value.comparison_previous_period)||!object(value.series)||typeof value.file_sample_truncated!=='boolean')throw new Error('The server returned invalid analytics.');
 const totals=value.totals,series=value.series,comparison=value.comparison_previous_period;
 const nat=(n:unknown):n is number=>typeof n==='number'&&Number.isSafeInteger(n)&&n>=0;
 if(!nat(totals.stored_files)||!nat(totals.storage_bytes)||!nat(totals.period_uploads)||typeof comparison.files_delta!=='number'||!Number.isSafeInteger(comparison.files_delta)||!Array.isArray(series.days)||!Array.isArray(series.uploads)||!Array.isArray(series.storage_bytes_added)||series.days.length!==series.uploads.length||series.days.length!==series.storage_bytes_added.length||series.days.length>365||!series.days.every((d:unknown)=>typeof d==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(d))||!series.uploads.every(nat)||!series.storage_bytes_added.every(nat))throw new Error('The server returned invalid analytics values.');
 return {storedFiles:totals.stored_files,storageBytes:totals.storage_bytes,periodUploads:totals.period_uploads,filesDelta:comparison.files_delta,sampled:value.file_sample_truncated,days:series.days,uploads:series.uploads,bytesAdded:series.storage_bytes_added};
}
