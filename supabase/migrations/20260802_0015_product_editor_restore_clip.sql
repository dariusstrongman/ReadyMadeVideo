-- Keep post-autosave clip deletion undoable through the typed operation log.
alter table public.editor_operations
  drop constraint if exists editor_operations_operation_type_check;

alter table public.editor_operations
  add constraint editor_operations_operation_type_check check (operation_type in
    ('reorder_clip','trim_clip','split_clip','delete_clip','restore_clip',
     'update_caption','set_music_gain','toggle_graphic'));
