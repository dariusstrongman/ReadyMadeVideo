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
 (id,batch_id,project_id,user_id,candidate_run_id,dimensions,overall_publishability_score,publishable,blocking_issues,technical_qc_passed,rendered_media_qc_passed,tournament_eligible,rendered_media_qc,created_by) values
 ('d5000000-0000-0000-0000-000000000001','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','{}',80,true,'[]',true,true,true,'{"passed":true}','15000000-0000-0000-0000-000000000002');
insert into public.publishability_reports
 (id,batch_id,project_id,user_id,candidate_run_id,dimensions,overall_publishability_score,publishable,blocking_issues,technical_qc_passed,rendered_media_qc_passed,tournament_eligible,rendered_media_qc,created_by) values
 ('d5000000-0000-0000-0000-000000000002','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000002','{}',99,false,'["render_qc: missing_video_stream"]',true,false,false,'{"passed":false}','15000000-0000-0000-0000-000000000002');
select pg_temp.expect_editorial_rejected($$insert into public.tournament_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,version,candidate_run_ids,pairwise_comparisons,bracket,winner_candidate_run_id,winner_reasoning,human_ceiling_comparison,created_by) values
 ('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001',1,array['b5000000-0000-0000-0000-000000000001'::uuid,'b5000000-0000-0000-0000-000000000002'::uuid],'[]','[]','b5000000-0000-0000-0000-000000000002','[]','{}','15000000-0000-0000-0000-000000000002')$$,'rendered-media-ineligible tournament winner');
insert into public.tournament_runs
 (id,batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,version,candidate_run_ids,pairwise_comparisons,bracket,winner_candidate_run_id,winner_reasoning,human_ceiling_comparison,created_by) values
 ('e5000000-0000-0000-0000-000000000001','bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001',1,array['b5000000-0000-0000-0000-000000000001'::uuid,'b5000000-0000-0000-0000-000000000002'::uuid],'[]','[]','b5000000-0000-0000-0000-000000000001','["higher evidence score"]','{}','15000000-0000-0000-0000-000000000002');

select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('bc000000-0000-0000-0000-000000000002','25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','foreign',1,'initial','x','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000002/projects/25000000-0000-0000-0000-000000000002/editorial-intelligence/x.mp4','15000000-0000-0000-0000-000000000002')$$,'cross-project candidate');
select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','initial-a',4,'initial','x','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/x.mp4','15000000-0000-0000-0000-000000000002')$$,'duplicate candidate key');
select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,fabricated_footage,created_by) values
 ('bc000000-0000-0000-0000-000000000003','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','fabricated',1,'initial','x','{}','{"fabricatedFootage":true}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/fabricated.mp4',true,'15000000-0000-0000-0000-000000000002')$$,'fabricated footage true');
select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('bc000000-0000-0000-0000-000000000004','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','bad-path',1,'initial','x','{}','{"fabricatedFootage":false}','{}','exports','users/foreign/projects/foreign/editorial-intelligence/bad.mp4','15000000-0000-0000-0000-000000000002')$$,'invalid preview storage path');
select pg_temp.expect_editorial_rejected($$insert into public.candidate_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,parent_candidate_run_id,candidate_key,candidate_index,generation_kind,source_picture_candidate_id,variant_config,manifest,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('bc000000-0000-0000-0000-000000000005','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','bad-parent',1,'revised','x','{}','{"fabricatedFootage":false}','{}','exports','users/15000000-0000-0000-0000-000000000001/projects/25000000-0000-0000-0000-000000000001/editorial-intelligence/bad-parent.mp4','15000000-0000-0000-0000-000000000002')$$,'invalid revised parent lineage');
select pg_temp.expect_editorial_rejected($$insert into public.critic_runs(batch_id,project_id,user_id,candidate_run_id,critic_kind,score,passed,evidence,issues,revision_requests,consistency_hash,created_by) values('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','b5000000-0000-0000-0000-000000000001','captions',50,false,'[{}]','[]','[]',repeat('b',64),'15000000-0000-0000-0000-000000000002')$$,'cross-project critic');
select pg_temp.expect_editorial_rejected($$insert into public.publishability_reports(batch_id,project_id,user_id,candidate_run_id,dimensions,overall_publishability_score,publishable,blocking_issues,technical_qc_passed,created_by) values('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','b5000000-0000-0000-0000-000000000001','{}',50,false,'[]',false,'15000000-0000-0000-0000-000000000002')$$,'cross-project publishability');
select pg_temp.expect_editorial_rejected($$insert into public.tournament_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,version,candidate_run_ids,pairwise_comparisons,bracket,winner_candidate_run_id,winner_reasoning,human_ceiling_comparison,created_by) values
 ('bc000000-0000-0000-0000-000000000009','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001',2,array['b5000000-0000-0000-0000-000000000001'::uuid,'b5000000-0000-0000-0000-000000000002'::uuid],'[]','[]','b5000000-0000-0000-0000-000000000003','[]','{}','15000000-0000-0000-0000-000000000002')$$,'winner outside tournament candidates');
select pg_temp.expect_editorial_rejected($$insert into public.tournament_runs
 (batch_id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,color_run_id,version,candidate_run_ids,pairwise_comparisons,bracket,winner_candidate_run_id,winner_reasoning,human_ceiling_comparison,created_by) values
 ('bc000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','35000000-0000-0000-0000-000000000001','45000000-0000-0000-0000-000000000001','55000000-0000-0000-0000-000000000001','75000000-0000-0000-0000-000000000001','85000000-0000-0000-0000-000000000001','95000000-0000-0000-0000-000000000001','a5000000-0000-0000-0000-000000000001',1,array['b5000000-0000-0000-0000-000000000001'::uuid,'b5000000-0000-0000-0000-000000000002'::uuid],'[]','[]','b5000000-0000-0000-0000-000000000001','[]','{}','15000000-0000-0000-0000-000000000002')$$,'duplicate tournament version');
select pg_temp.expect_editorial_rejected($$update public.candidate_runs set candidate_key='changed' where id='b5000000-0000-0000-0000-000000000001'$$,'immutable candidate update');
select pg_temp.expect_editorial_rejected($$delete from public.candidate_runs where id='b5000000-0000-0000-0000-000000000001'$$,'immutable candidate delete');
select pg_temp.expect_editorial_rejected($$update public.critic_runs set score=1 where id='c5000000-0000-0000-0000-000000000001'$$,'immutable critic update');
select pg_temp.expect_editorial_rejected($$delete from public.critic_runs where id='c5000000-0000-0000-0000-000000000001'$$,'immutable critic delete');
select pg_temp.expect_editorial_rejected($$update public.publishability_reports set publishable=false where id='d5000000-0000-0000-0000-000000000001'$$,'immutable report update');
select pg_temp.expect_editorial_rejected($$delete from public.publishability_reports where id='d5000000-0000-0000-0000-000000000001'$$,'immutable report delete');
select pg_temp.expect_editorial_rejected($$update public.tournament_runs set version=2 where id='e5000000-0000-0000-0000-000000000001'$$,'immutable tournament update');
select pg_temp.expect_editorial_rejected($$delete from public.tournament_runs where id='e5000000-0000-0000-0000-000000000001'$$,'immutable tournament delete');

-- Product Editor Phase 1: real PostgreSQL ancestry, concurrency, immutable
-- revisions, append-only operations, and exact render-version binding.
insert into public.timelines
 (id,project_id,user_id,version,timeline_json,lineage,is_immutable) values
 ('f5000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001',10,'{"version":1}','product_editor',true),
 ('f5000000-0000-0000-0000-000000000002','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001',11,'{"version":1}','product_editor',true);
insert into public.editor_documents
 (id,project_id,user_id,candidate_run_id,timeline_id,version,document,created_by) values
 ('f6000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f5000000-0000-0000-0000-000000000001',1,
  '{"schemaVersion":1,"projectId":"25000000-0000-0000-0000-000000000001","candidateRunId":"b5000000-0000-0000-0000-000000000001"}',
  '15000000-0000-0000-0000-000000000001');
insert into public.editor_documents
 (id,project_id,user_id,candidate_run_id,parent_document_id,timeline_id,version,document,created_by) values
 ('f6000000-0000-0000-0000-000000000002','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000001','f5000000-0000-0000-0000-000000000002',2,
  '{"schemaVersion":1,"projectId":"25000000-0000-0000-0000-000000000001","candidateRunId":"b5000000-0000-0000-0000-000000000001"}',
  '15000000-0000-0000-0000-000000000001');
select pg_temp.expect_editorial_rejected($$insert into public.editor_documents
 (project_id,user_id,candidate_run_id,timeline_id,version,document,created_by) values
 ('25000000-0000-0000-0000-000000000002','15000000-0000-0000-0000-000000000002','b5000000-0000-0000-0000-000000000001','f5000000-0000-0000-0000-000000000001',3,
  '{"schemaVersion":1,"projectId":"25000000-0000-0000-0000-000000000002","candidateRunId":"b5000000-0000-0000-0000-000000000001"}',
  '15000000-0000-0000-0000-000000000002')$$,'cross-project editor candidate');
select pg_temp.expect_editorial_rejected($$insert into public.editor_documents
 (project_id,user_id,candidate_run_id,parent_document_id,timeline_id,version,document,created_by) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000001','f5000000-0000-0000-0000-000000000002',4,
  '{"schemaVersion":1,"projectId":"25000000-0000-0000-0000-000000000001","candidateRunId":"b5000000-0000-0000-0000-000000000001"}',
  '15000000-0000-0000-0000-000000000001')$$,'invalid editor parent version');
select pg_temp.expect_editorial_rejected($$insert into public.editor_documents
 (project_id,user_id,candidate_run_id,parent_document_id,timeline_id,version,document,created_by) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000001','f5000000-0000-0000-0000-000000000002',2,
  '{"schemaVersion":1,"projectId":"25000000-0000-0000-0000-000000000001","candidateRunId":"b5000000-0000-0000-0000-000000000001"}',
  '15000000-0000-0000-0000-000000000001')$$,'duplicate editor version');
insert into public.editor_operations
 (id,project_id,user_id,candidate_run_id,base_document_id,result_document_id,operation_id,operation_index,operation_type,target_id,actor,operation,client_timestamp) values
 ('f7000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000002','f8000000-0000-0000-0000-000000000001',1,'trim_clip','clip-a','user','{"type":"trim_clip"}',clock_timestamp());
select pg_temp.expect_editorial_rejected($$insert into public.editor_operations
 (project_id,user_id,candidate_run_id,base_document_id,result_document_id,operation_id,operation_index,operation_type,target_id,actor,operation,client_timestamp) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000002','f6000000-0000-0000-0000-000000000001','f8000000-0000-0000-0000-000000000002',2,'delete_clip','clip-a','ai','{"type":"delete_clip"}',clock_timestamp())$$,'invalid operation revision direction');
select pg_temp.expect_editorial_rejected($$insert into public.editor_operations
 (project_id,user_id,candidate_run_id,base_document_id,result_document_id,operation_id,operation_index,operation_type,target_id,actor,operation,client_timestamp) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','b5000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000002','f8000000-0000-0000-0000-000000000003',1,'delete_clip','clip-a','user','{"type":"delete_clip"}',clock_timestamp())$$,'duplicate operation index');
insert into public.pipeline_jobs
 (id,project_id,user_id,kind,status,params) values
 ('f9000000-0000-0000-0000-000000000001','25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','final_render','completed',
  '{"editor_document_id":"f6000000-0000-0000-0000-000000000002","editor_document_version":2,"timeline_id":"f5000000-0000-0000-0000-000000000002"}');
insert into public.editor_render_requests
 (project_id,user_id,editor_document_id,editor_document_version,pipeline_job_id) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000002',2,'f9000000-0000-0000-0000-000000000001');
select pg_temp.expect_editorial_rejected($$insert into public.editor_render_requests
 (project_id,user_id,editor_document_id,editor_document_version,pipeline_job_id) values
 ('25000000-0000-0000-0000-000000000001','15000000-0000-0000-0000-000000000001','f6000000-0000-0000-0000-000000000002',1,'f9000000-0000-0000-0000-000000000001')$$,'render version mismatch');
select pg_temp.expect_editorial_rejected($$update public.editor_documents set version=9 where id='f6000000-0000-0000-0000-000000000001'$$,'immutable editor document update');
select pg_temp.expect_editorial_rejected($$delete from public.editor_documents where id='f6000000-0000-0000-0000-000000000001'$$,'immutable editor document delete');
select pg_temp.expect_editorial_rejected($$update public.editor_operations set actor='ai' where id='f7000000-0000-0000-0000-000000000001'$$,'immutable editor operation update');
select pg_temp.expect_editorial_rejected($$delete from public.editor_render_requests where pipeline_job_id='f9000000-0000-0000-0000-000000000001'$$,'immutable editor render binding delete');

rollback;
