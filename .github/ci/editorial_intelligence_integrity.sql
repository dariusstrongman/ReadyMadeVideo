\set ON_ERROR_STOP on
begin;

create or replace function pg_temp.expect_editorial_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin execute statement; exception when others then rejected := true;
    raise notice 'expected rejection [%]: %',label,sqlerrm; end;
  if not rejected then raise exception 'editorial assertion did not reject: %',label; end if;
end $$;

insert into auth.users(id,email) values
 ('15000000-0000-0000-0000-000000000001','editorial-owner@example.test'),
 ('15000000-0000-0000-0000-000000000002','editorial-other@example.test');
insert into public.projects(id,user_id,name,status) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','Editorial Project','ready'),
 ('25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','Foreign Editorial','ready');
insert into public.preproduction_runs
 (id,project_id,user_id,version,status,request,creative_treatment,capture_quality_report,composition_by_segment,story_variants) values
 ('35000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001',1,'ready','{}','{}','{}','{}','{}');
insert into public.picture_edit_runs
 (id,project_id,user_id,preproduction_run_id,version,status,request,visual_rhythm_plans,candidates,selected_candidate_id) values
 ('45000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001',1,'ready','{}','{}','[]','picture-a');
insert into public.music_sound_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,selected_candidate_id,version,status,request,music_plan) values
 ('55000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','picture-a',1,'ready','{}','{}');
insert into public.licensed_music_assets
 (id,project_id,user_id,music_sound_run_id,picture_edit_run_id,selected_candidate_id,version,storage_bucket,storage_path,filename,content_type,size_bytes,license_metadata,media_info,waveform_analysis,attached_by) values
 ('65000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','picture-a',1,'raw-footage','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/licensed-music/a.wav','a.wav','audio/wav',10,'{"confirmedByOperator":true,"licenseReference":"x"}','{}','{"analysisSource":"actual_waveform"}','15000000-0000-0000-0000-000000000002');
insert into public.audio_mix_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,licensed_music_asset_id,selected_candidate_id,version,status,target_vs_actual,mix_instructions,audio_qc,preview_storage_bucket,preview_storage_path,picture_timing_changed) values
 ('75000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','65000000-0000-0000-0000-000000000001','picture-a',1,'qc_passed','{}','{}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/audio-previews/v1.mp4',false);
insert into public.graphics_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,selected_candidate_id,version,status,request,platform_preset,brand_template,graphics_timeline,picture_timing_changed,audio_changed,created_by) values
 ('85000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','picture-a',1,'ready','{}','{}','{}','{"pictureTimingChanged":false,"audioChanged":false}',false,false,'15000000-0000-0000-0000-000000000002');
insert into public.caption_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,selected_candidate_id,version,status,caption_timeline,timing_provenance,overlaps_detected,picture_timing_changed,created_by) values
 ('95000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','picture-a',1,'ready','{}','[]',0,false,'15000000-0000-0000-0000-000000000002');
insert into public.color_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,selected_candidate_id,version,status,color_instructions,render_qc,preview_storage_bucket,preview_storage_path,non_destructive,picture_timing_changed,audio_changed,created_by) values
 ('a5000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','picture-a',1,'qc_passed','{}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/visual-finishing/v1.mp4',true,false,false,'15000000-0000-0000-0000-000000000002');

insert into public.candidate_runs
 (id,batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('b5000000-0000-0000-0000-000000000001','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','initial-a',1,'initial','picture-a','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/v1/a.mp4','15000000-0000-0000-0000-000000000002'),
 ('b5000000-0000-0000-0000-000000000002','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','initial-b',2,'initial','picture-b','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/v1/b.mp4','15000000-0000-0000-0000-000000000002');
insert into public.candidate_runs
 (id,batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,parent_candidate_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('b5000000-0000-0000-0000-000000000003','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','revised-a',3,'revised','picture-a','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/v1/r.mp4','15000000-0000-0000-0000-000000000002');
insert into public.critic_runs
 (id,batch_id,project_id,user_id,candidate_run_id,critic_kind,score,passed,evidence,issues,revision_requests,consistency_hash,created_by) values
 ('c5000000-0000-0000-0000-000000000001','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','hook_effectiveness',80,true,'[{"metric":"hook","observed":1,"target":1,"sourceRef":"segment","weight":1,"contribution":80,"explanation":"supported"}]','[]','[]',repeat('a',64),'15000000-0000-0000-0000-000000000002');
insert into public.publishability_reports
 (id,batch_id,project_id,user_id,candidate_run_id,dimensions,overall_publishability_score,publishable,blocking_issues,technical_qc_passed,created_by) values
 ('d5000000-0000-0000-0000-000000000001','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','{}',80,true,'[]',true,'15000000-0000-0000-0000-000000000002');
insert into public.tournament_runs
 (id,batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,version,candidate_run_ids,pairwise_comparisons,bracket,winner_candidate_run_id,winner_reasoning,human_ceiling_comparison,created_by) values
 ('e5000000-0000-0000-0000-000000000001','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001',1,array['b5000000-0000-0000-0000-000000000001'::uuid,'b5000000-0000-0000-0000-000000000002'::uuid],'[]','[]','b5000000-0000-0000-0000-000000000001','["higher evidence score"]','{}','15000000-0000-0000-0000-000000000002');

select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('bc000000-0000-0000-0000-000000000002','25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','foreign',1,'initial','x','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000002/projects/25000000-0000-0000-0000-000000000002/editorial-intelligence/x.mp4','15000000-0000-0000-0000-000000000002')$$,'cross-project candidate');
select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','initial-a',4,'initial','x','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/x.mp4','15000000-0000-0000-0000-000000000002')$$,'duplicate candidate key');
select pg_temp.expect_editorial_rejected($$insert into public.critic_runs(batch_id,project_id,user_id,candidate_run_id,critic_kind,score,passed,evidence,issues,revision_requests,consistency_hash,created_by) values('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','b5000000-0000-0000-0000-000000000001','captions',50,false,'[{}]','[]','[]',repeat('b',64),'15000000-0000-0000-0000-000000000002')$$,'cross-project critic');
select pg_temp.expect_editorial_rejected($$insert into public.publishability_reports(batch_id,project_id,user_id,candidate_run_id,dimensions,overall_publishability_score,publishable,blocking_issues,technical_qc_passed,created_by) values('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','b5000000-0000-0000-0000-000000000001','{}',50,false,'[]',false,'15000000-0000-0000-0000-000000000002')$$,'cross-project publishability');
select pg_temp.expect_editorial_rejected($$insert into public.tournament_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,version,candidate_run_ids,pairwise_comparisons,bracket,winner_candidate_run_id,winner_reasoning,human_ceiling_comparison,created_by) values
 ('bc000000-0000-0000-0000-000000000009','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001',2,array['b5000000-0000-0000-0000-000000000001'::uuid,'b5000000-0000-0000-0000-000000000002'::uuid],'[]','[]','b5000000-0000-0000-0000-000000000003','[]','{}','15000000-0000-0000-0000-000000000002')$$,'winner outside tournament candidates');
select pg_temp.expect_editorial_rejected($$update public.candidate_runs set candidate_key='changed' where id='b5000000-0000-0000-0000-000000000001'$$,'immutable candidate update');
select pg_temp.expect_editorial_rejected($$delete from public.candidate_runs where id='b5000000-0000-0000-0000-000000000001'$$,'immutable candidate delete');
select pg_temp.expect_editorial_rejected($$update public.critic_runs set score=1 where id='c5000000-0000-0000-0000-000000000001'$$,'immutable critic update');
select pg_temp.expect_editorial_rejected($$delete from public.critic_runs where id='c5000000-0000-0000-0000-000000000001'$$,'immutable critic delete');
select pg_temp.expect_editorial_rejected($$update public.publishability_reports set publishable=false where id='d5000000-0000-0000-0000-000000000001'$$,'immutable report update');
select pg_temp.expect_editorial_rejected($$delete from public.publishability_reports where id='d5000000-0000-0000-0000-000000000001'$$,'immutable report delete');
select pg_temp.expect_editorial_rejected($$update public.tournament_runs set version=2 where id='e5000000-0000-0000-0000-000000000001'$$,'immutable tournament update');
select pg_temp.expect_editorial_rejected($$delete from public.tournament_runs where id='e5000000-0000-0000-0000-000000000001'$$,'immutable tournament delete');

rollback;
