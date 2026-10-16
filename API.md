# Dashboard contract

Frontend: vanilla HTML/CSS/JS in `web/`, served by Python on port 5000.
Use relative fetch, JSON requests with `X-Studio-Request: 1`. Errors: `{"error":"plain message"}`.
No dependencies, frameworks or build step required. Do not use emojis.

GET /api/overview -> {projects:[Project], statuses:[...], pillars:[...], settings:{providers:[{name,ready,missing:[key]}],ffmpeg:bool}, jobs:{id:{running,message,error}}}
GET /api/projects/{id} -> Project (enriched with fact_check, next_action, analytics_report, files, job)
POST /api/projects {topic,pillar,series?,next_idea?,quality?:"4K"|"HD"} -> Project.
POST /api/projects/{id}/{action} body -> refreshed Project, except autopilot returns {running,message}.

Project fields: id, topic, pillar, series, next_idea, quality, status, created_at, updated_at,
score:{curiosity,audience,story,visuals,evergreen,monetization,difficulty,total,confirmed,override_reason},
questions:[string], sources:[{url,title,text,publication_date}], claims:[{id,claim,source_url,source_title,quote,publication_date,date_checked,confidence,status,verified_by,note,metric,issues:[]}],
script:{scenes:[{id,section,text,claim_ids:[],footage_query}]}, revisions:[{revision,at,reason,script}],
approvals:{topic?,research?,script?,packaging?}, footage:[{scene_id,path,width,height,upscaled,photographer,url}], titles:[{family,title}],
thumbnail:{object,emotion,text}, selected_title, description, youtube_url, analytics:{ctr,average_percentage_viewed,retention:[{time,percent}]},
history:[{at,from,to,actor}], fact_check:{blocking:[],warnings:[],words,estimated_minutes,passed}, qa:{required:[{label,passed}],advisory:[{label,passed}],passed,upscaled_clips},
next_action (string), files:[{name,url}], job:{running,message,error}|null.

Actions:
- score: {curiosity:0..25,audience:0..20,story:0..20,visuals:0..10,evergreen:0..10,monetization:0..10,difficulty:0..5,confirmed:bool,override_reason:string}
- gate: {gate:"topic"|"research"|"script"|"packaging"|"published", note?:string, youtube_url?:string}. Only valid at correct stage; gates advance exactly once, no skip.
- settings: {next_idea,quality,burn_captions:boolean} (series/topic immutable after creation).
- claim: {id?:string,claim,quote,source_index:integer (0 based),metric?:string}. Editing always invalidates research approval and sets deterministic verification. To add sources: source {url,title,text,publication_date}. Human supplied page text is labelled, not a live fetch.
- verify_claim: {id,status:"verified"|"rejected"|"needs_checking",note:string}; hand verification requires note.
- script: {scenes:[{id,section,text,claim_ids:[],footage_query}]} keeps every revision, rechecks. Also import_script {text:string} uses same JSON format as export.
- packaging: {selected_title,thumbnail:{object,emotion,text},description}; gate validates.
- analytics: {ctr:number 0..100,average_percentage_viewed:0..100,retention_csv?:string} saves metrics.
- music: {filename:string,data:base64,rights_confirmed:true}; only audio <=50MB.
- autopilot: {} starts background job, poll project/overview every 2s until done. Provider errors shown plainly; never fake data.
- dry_run: {} -> {chunks:[{index,characters,checksum,text}],total_characters}; free.
- suggest_score: {} -> Project with score_suggestion (does NOT set score or confirm).
- run: {step:"research"|"script"|"voice"|"footage"|"cut"|"qa"|"titles"} starts same background runner for that single step.
- rerun: {} resumes invalidated approved production at current stage after gates are restored.

GET /api/projects/{id}/export-script -> attachment JSON scenes
GET /files/{id}/{relative path} -> allowlisted media/download file; support range for audio/video.
GET /api/settings -> settings. Keys are NEVER returned, only missing names.

Pages: / (production projects list with filtering/search and real empty state); /project/{id} (all editorial stages, human gates, exports, analytics); /settings (provider readiness and configuration guidance); /analytics (channel comparison using actual projects, no fake chart data).
Status order exact: IDEA, SELECTED, RESEARCHING, RESEARCH_COMPLETE, SCRIPTING, SCRIPT_REVIEW, APPROVED, VOICE, VISUALS, EDITING, QA, THUMBNAIL, UPLOAD_READY, PUBLISHED, ANALYZING.
The default dashboard is empty. No seeded fake production records. All navigation/buttons must work.
