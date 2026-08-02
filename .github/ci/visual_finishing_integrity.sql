\set ON_ERROR_STOP on
begin;

create or replace function pg_temp.expect_visual_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin execute statement;
  exception when others then rejected := true; raise notice 'expected rejection [%]: %', label, sqlerrm;
  end;
  if not rejected then raise exception 'visual-finishing assertion did not reject: %', label; end if;
end $$;

insert into auth.users (id,email) values
 ('14000000-0000-0000-0000-000000000001','visual-owner@example.test'),
 ('14000000-0000-0000-0000-000000000002','visual-other@example.test');
insert into public.projects (id,user_id,name,status) values
 ('24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','Visual Project','ready'),
 ('24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','Foreign Visual','ready');
insert into public.preproduction_runs
 (id,project_id,user_id,version,status,request,creative_treatment,capture_quality_report,composition_by_segment,story_variants) values
 ('34000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001',1,'ready','{}','{}','{}','{}','{}'),
 ('34000000-0000-0000-0000-000000000002','24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002',1,'ready','{}','{}','{}','{}','{}');
insert into public.picture_edit_runs
 (id,project_id,user_id,preproduction_run_id,version,status,request,visual_rhythm_plans,candidates,selected_candidate_id) values
 ('44000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001',1,'ready','{}','{}','[]','arc'),
 ('44000000-0000-0000-0000-000000000002','24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','34000000-0000-0000-0000-000000000002',1,'ready','{}','{}','[]','foreign');
insert into public.music_sound_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,selected_candidate_id,version,status,request,music_plan) values
 ('54000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','arc',1,'ready','{}','{}'),
 ('54000000-0000-0000-0000-000000000002','24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','34000000-0000-0000-0000-000000000002','44000000-0000-0000-0000-000000000002','foreign',1,'ready','{}','{}');
insert into public.licensed_music_assets
 (id,project_id,user_id,music_sound_run_id,picture_edit_run_id,selected_candidate_id,version,storage_bucket,storage_path,filename,content_type,size_bytes,license_metadata,media_info,waveform_analysis,attached_by) values
 ('64000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','arc',1,'raw-footage','users/14000000-0000-0000-0000-000000000001/projects/24000000-0000-0000-0000-000000000001/licensed-music/x/a.wav','a.wav','audio/wav',10,'{"confirmedByOperator":true,"licenseReference":"x"}','{}','{"analysisSource":"actual_waveform"}','14000000-0000-0000-0000-000000000002'),
 ('64000000-0000-0000-0000-000000000002','24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','54000000-0000-0000-0000-000000000002','44000000-0000-0000-0000-000000000002','foreign',1,'raw-footage','users/14000000-0000-0000-0000-000000000002/projects/24000000-0000-0000-0000-000000000002/licensed-music/x/b.wav','b.wav','audio/wav',10,'{"confirmedByOperator":true,"licenseReference":"y"}','{}','{"analysisSource":"actual_waveform"}','14000000-0000-0000-0000-000000000002');
insert into public.audio_mix_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,licensed_music_asset_id,selected_candidate_id,version,status,target_vs_actual,mix_instructions,audio_qc,preview_storage_bucket,preview_storage_path,picture_timing_changed) values
 ('74000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','64000000-0000-0000-0000-000000000001','arc',1,'qc_passed','{}','{}','{}','exports','users/14000000-0000-0000-0000-000000000001/projects/24000000-0000-0000-0000-000000000001/audio-previews/v1.mp4',false),
 ('74000000-0000-0000-0000-000000000002','24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','34000000-0000-0000-0000-000000000002','44000000-0000-0000-0000-000000000002','54000000-0000-0000-0000-000000000002','64000000-0000-0000-0000-000000000002','foreign',1,'qc_passed','{}','{}','{}','exports','users/14000000-0000-0000-0000-000000000002/projects/24000000-0000-0000-0000-000000000002/audio-previews/v1.mp4',false);

insert into public.graphics_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,selected_candidate_id,version,status,request,platform_preset,brand_template,graphics_timeline,picture_timing_changed,audio_changed,created_by) values
 ('84000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','74000000-0000-0000-0000-000000000001','arc',1,'ready','{}','{}','{}','{"pictureTimingChanged":false,"audioChanged":false}',false,false,'14000000-0000-0000-0000-000000000002');
insert into public.caption_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,selected_candidate_id,version,status,caption_timeline,timing_provenance,overlaps_detected,picture_timing_changed,created_by) values
 ('94000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','74000000-0000-0000-0000-000000000001','84000000-0000-0000-0000-000000000001','arc',1,'ready','{}','[]',0,false,'14000000-0000-0000-0000-000000000002');
insert into public.color_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,selected_candidate_id,version,status,color_instructions,render_qc,preview_storage_bucket,preview_storage_path,non_destructive,picture_timing_changed,audio_changed,created_by) values
 ('a4000000-0000-0000-0000-000000000001','24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','74000000-0000-0000-0000-000000000001','84000000-0000-0000-0000-000000000001','94000000-0000-0000-0000-000000000001','arc',1,'qc_passed','{}','{}','exports','users/14000000-0000-0000-0000-000000000001/projects/24000000-0000-0000-0000-000000000001/visual-finishing/v1.mp4',true,false,false,'14000000-0000-0000-0000-000000000002');

select pg_temp.expect_visual_rejected($$insert into public.graphics_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,selected_candidate_id,version,status,request,platform_preset,brand_template,graphics_timeline,created_by) values
 ('24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','34000000-0000-0000-0000-000000000002','44000000-0000-0000-0000-000000000002','54000000-0000-0000-0000-000000000002','74000000-0000-0000-0000-000000000001','arc',2,'ready','{}','{}','{}','{"pictureTimingChanged":false,"audioChanged":false}','14000000-0000-0000-0000-000000000002')$$, 'cross-project graphics');
select pg_temp.expect_visual_rejected($$insert into public.graphics_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,selected_candidate_id,version,status,request,platform_preset,brand_template,graphics_timeline,created_by) values
 ('24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','74000000-0000-0000-0000-000000000001','arc',1,'ready','{}','{}','{}','{"pictureTimingChanged":false,"audioChanged":false}','14000000-0000-0000-0000-000000000002')$$, 'graphics version collision');
select pg_temp.expect_visual_rejected($$insert into public.caption_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,selected_candidate_id,version,status,caption_timeline,timing_provenance,overlaps_detected,created_by) values
 ('24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','34000000-0000-0000-0000-000000000002','44000000-0000-0000-0000-000000000002','54000000-0000-0000-0000-000000000002','74000000-0000-0000-0000-000000000002','84000000-0000-0000-0000-000000000001','foreign',2,'ready','{}','[]',0,'14000000-0000-0000-0000-000000000002')$$, 'cross-project captions');
select pg_temp.expect_visual_rejected($$insert into public.caption_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,selected_candidate_id,version,status,caption_timeline,timing_provenance,overlaps_detected,created_by) values
 ('24000000-0000-0000-0000-000000000001','14000000-0000-0000-0000-000000000001','34000000-0000-0000-0000-000000000001','44000000-0000-0000-0000-000000000001','54000000-0000-0000-0000-000000000001','74000000-0000-0000-0000-000000000001','84000000-0000-0000-0000-000000000001','arc',2,'ready','{}','[]',1,'14000000-0000-0000-0000-000000000002')$$, 'caption overlap');
select pg_temp.expect_visual_rejected($$insert into public.color_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,audio_mix_run_id,graphics_run_id,caption_run_id,selected_candidate_id,version,status,color_instructions,render_qc,preview_storage_bucket,preview_storage_path,created_by) values
 ('24000000-0000-0000-0000-000000000002','14000000-0000-0000-0000-000000000002','34000000-0000-0000-0000-000000000002','44000000-0000-0000-0000-000000000002','54000000-0000-0000-0000-000000000002','74000000-0000-0000-0000-000000000002','84000000-0000-0000-0000-000000000001','94000000-0000-0000-0000-000000000001','foreign',2,'qc_passed','{}','{}','exports','users/x/projects/y/visual-finishing/v2.mp4','14000000-0000-0000-0000-000000000002')$$, 'cross-project color');

select pg_temp.expect_visual_rejected($$update public.graphics_runs set status='render_failed' where id='84000000-0000-0000-0000-000000000001'$$, 'immutable graphics update');
select pg_temp.expect_visual_rejected($$delete from public.graphics_runs where id='84000000-0000-0000-0000-000000000001'$$, 'immutable graphics delete');
select pg_temp.expect_visual_rejected($$update public.caption_runs set status='no_speech' where id='94000000-0000-0000-0000-000000000001'$$, 'immutable caption update');
select pg_temp.expect_visual_rejected($$delete from public.caption_runs where id='94000000-0000-0000-0000-000000000001'$$, 'immutable caption delete');
select pg_temp.expect_visual_rejected($$update public.color_runs set status='qc_failed' where id='a4000000-0000-0000-0000-000000000001'$$, 'immutable color update');
select pg_temp.expect_visual_rejected($$delete from public.color_runs where id='a4000000-0000-0000-0000-000000000001'$$, 'immutable color delete');

rollback;
