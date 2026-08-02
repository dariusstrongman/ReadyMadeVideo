\set ON_ERROR_STOP on
begin;

create or replace function pg_temp.expect_audio_render_rejected(statement text, label text)
returns void language plpgsql as $$
declare rejected boolean := false;
begin
  begin execute statement;
  exception when others then rejected := true;
    raise notice 'expected rejection [%]: %', label, sqlerrm;
  end;
  if not rejected then raise exception 'audio-render assertion did not reject: %', label; end if;
end $$;

insert into auth.users (id,email) values
 ('13000000-0000-0000-0000-000000000001','audio-owner@example.test'),
 ('13000000-0000-0000-0000-000000000002','audio-other@example.test');
insert into public.projects (id,user_id,name,status) values
 ('23000000-0000-0000-0000-000000000001','13000000-0000-0000-0000-000000000001','Audio Project','ready'),
 ('23000000-0000-0000-0000-000000000002','13000000-0000-0000-0000-000000000002','Foreign Audio','ready');
insert into public.preproduction_runs
 (id,project_id,user_id,version,status,request,creative_treatment,
  capture_quality_report,composition_by_segment,story_variants) values
 ('33000000-0000-0000-0000-000000000001','23000000-0000-0000-0000-000000000001',
  '13000000-0000-0000-0000-000000000001',1,'ready','{}','{}','{}','{}','{}'),
 ('33000000-0000-0000-0000-000000000002','23000000-0000-0000-0000-000000000002',
  '13000000-0000-0000-0000-000000000002',1,'ready','{}','{}','{}','{}','{}');
insert into public.picture_edit_runs
 (id,project_id,user_id,preproduction_run_id,version,status,request,
  visual_rhythm_plans,candidates,selected_candidate_id) values
 ('43000000-0000-0000-0000-000000000001','23000000-0000-0000-0000-000000000001',
  '13000000-0000-0000-0000-000000000001','33000000-0000-0000-0000-000000000001',
  1,'ready','{}','{}','[]','treatment_arc'),
 ('43000000-0000-0000-0000-000000000002','23000000-0000-0000-0000-000000000002',
  '13000000-0000-0000-0000-000000000002','33000000-0000-0000-0000-000000000002',
  1,'ready','{}','{}','[]','kinetic_hook');
insert into public.music_sound_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,
  selected_candidate_id,version,status,request,music_plan) values
 ('53000000-0000-0000-0000-000000000001','23000000-0000-0000-0000-000000000001',
  '13000000-0000-0000-0000-000000000001','33000000-0000-0000-0000-000000000001',
  '43000000-0000-0000-0000-000000000001','treatment_arc',1,'ready','{}','{}'),
 ('53000000-0000-0000-0000-000000000002','23000000-0000-0000-0000-000000000002',
  '13000000-0000-0000-0000-000000000002','33000000-0000-0000-0000-000000000002',
  '43000000-0000-0000-0000-000000000002','kinetic_hook',1,'ready','{}','{}');

insert into public.licensed_music_assets
 (id,project_id,user_id,music_sound_run_id,picture_edit_run_id,selected_candidate_id,
  version,storage_bucket,storage_path,filename,content_type,size_bytes,
  license_metadata,media_info,waveform_analysis,attached_by) values
 ('63000000-0000-0000-0000-000000000001','23000000-0000-0000-0000-000000000001',
  '13000000-0000-0000-0000-000000000001','53000000-0000-0000-0000-000000000001',
  '43000000-0000-0000-0000-000000000001','treatment_arc',1,'raw-footage',
  'users/13000000-0000-0000-0000-000000000001/projects/23000000-0000-0000-0000-000000000001/licensed-music/x/track.wav',
  'track.wav','audio/wav',1000,
  '{"confirmedByOperator":true,"licenseReference":"license-1"}','{}',
  '{"analysisSource":"actual_waveform"}','13000000-0000-0000-0000-000000000002');

insert into public.audio_mix_runs
 (id,project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,
  licensed_music_asset_id,selected_candidate_id,version,status,target_vs_actual,
  mix_instructions,audio_qc,preview_storage_bucket,preview_storage_path,
  picture_timing_changed) values
 ('73000000-0000-0000-0000-000000000001','23000000-0000-0000-0000-000000000001',
  '13000000-0000-0000-0000-000000000001','33000000-0000-0000-0000-000000000001',
  '43000000-0000-0000-0000-000000000001','53000000-0000-0000-0000-000000000001',
  '63000000-0000-0000-0000-000000000001','treatment_arc',1,'qc_passed','{}','{}','{}',
  'exports','users/13000000-0000-0000-0000-000000000001/projects/23000000-0000-0000-0000-000000000001/audio-previews/v1.mp4',false);

select pg_temp.expect_audio_render_rejected(
 $$insert into public.licensed_music_assets
 (project_id,user_id,music_sound_run_id,picture_edit_run_id,selected_candidate_id,version,
 storage_bucket,storage_path,filename,content_type,size_bytes,license_metadata,media_info,
 waveform_analysis,attached_by) values
 ('23000000-0000-0000-0000-000000000002','13000000-0000-0000-0000-000000000002',
 '53000000-0000-0000-0000-000000000001','43000000-0000-0000-0000-000000000001',
 'treatment_arc',2,'raw-footage','users/x/projects/y/licensed-music/x/a.wav','a.wav',
 'audio/wav',10,'{"confirmedByOperator":true,"licenseReference":"x"}','{}',
 '{"analysisSource":"actual_waveform"}','13000000-0000-0000-0000-000000000002')$$,
 'cross-project licensed music');
select pg_temp.expect_audio_render_rejected(
 $$insert into public.licensed_music_assets
 (project_id,user_id,music_sound_run_id,picture_edit_run_id,selected_candidate_id,version,
 storage_bucket,storage_path,filename,content_type,size_bytes,license_metadata,media_info,
 waveform_analysis,attached_by) values
 ('23000000-0000-0000-0000-000000000001','13000000-0000-0000-0000-000000000001',
 '53000000-0000-0000-0000-000000000001','43000000-0000-0000-0000-000000000001',
 'treatment_arc',1,'raw-footage','users/13000000-0000-0000-0000-000000000001/projects/23000000-0000-0000-0000-000000000001/licensed-music/y/a.wav','a.wav',
 'audio/wav',10,'{"confirmedByOperator":true,"licenseReference":"x"}','{}',
 '{"analysisSource":"actual_waveform"}','13000000-0000-0000-0000-000000000002')$$,
 'duplicate attachment version');
select pg_temp.expect_audio_render_rejected(
 $$insert into public.audio_mix_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,
 licensed_music_asset_id,selected_candidate_id,version,status,target_vs_actual,
 mix_instructions,audio_qc,preview_storage_bucket,preview_storage_path,picture_timing_changed) values
 ('23000000-0000-0000-0000-000000000001','13000000-0000-0000-0000-000000000001',
 '33000000-0000-0000-0000-000000000001','43000000-0000-0000-0000-000000000001',
 '53000000-0000-0000-0000-000000000001','63000000-0000-0000-0000-000000000001',
 'kinetic_hook',2,'qc_passed','{}','{}','{}','exports',
 'users/13000000-0000-0000-0000-000000000001/projects/23000000-0000-0000-0000-000000000001/audio-previews/v2.mp4',false)$$,
 'selected candidate mismatch');
select pg_temp.expect_audio_render_rejected(
 $$insert into public.audio_mix_runs
 (project_id,user_id,preproduction_run_id,picture_edit_run_id,music_sound_run_id,
 licensed_music_asset_id,selected_candidate_id,version,status,target_vs_actual,
 mix_instructions,audio_qc,preview_storage_bucket,preview_storage_path,picture_timing_changed) values
 ('23000000-0000-0000-0000-000000000001','13000000-0000-0000-0000-000000000001',
 '33000000-0000-0000-0000-000000000001','43000000-0000-0000-0000-000000000001',
 '53000000-0000-0000-0000-000000000001','63000000-0000-0000-0000-000000000001',
 'treatment_arc',2,'qc_passed','{}','{}','{}','exports',
 'users/13000000-0000-0000-0000-000000000001/projects/23000000-0000-0000-0000-000000000001/audio-previews/v2.mp4',true)$$,
 'picture timing mutation');
select pg_temp.expect_audio_render_rejected(
 $$update public.licensed_music_assets set filename='changed.wav'
 where id='63000000-0000-0000-0000-000000000001'$$, 'immutable asset update');
select pg_temp.expect_audio_render_rejected(
 $$delete from public.licensed_music_assets
 where id='63000000-0000-0000-0000-000000000001'$$, 'immutable asset delete');
select pg_temp.expect_audio_render_rejected(
 $$update public.audio_mix_runs set status='qc_failed'
 where id='73000000-0000-0000-0000-000000000001'$$, 'immutable mix update');
select pg_temp.expect_audio_render_rejected(
 $$delete from public.audio_mix_runs
 where id='73000000-0000-0000-0000-000000000001'$$, 'immutable mix delete');

rollback;
