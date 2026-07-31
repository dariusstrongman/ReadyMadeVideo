/* Stromation intake capture — inserts into the Stromation Supabase `video_intake` table.
   The publishable (anon) key is safe in frontend code: RLS on video_intake allows
   INSERT only (no read/update/delete for anon), so signups cannot be scraped.
   Returns true on a confirmed save, throws on failure — callers must only show
   success when this resolves, never optimistically. */
(function () {
  var SB_URL = 'https://iadzcnzgbtuigyodeqas.supabase.co';
  var SB_KEY = 'sb_publishable_8qa-nssfdtEkCz-42wOSWQ_2P7S4Zj7';

  // Whitelisted columns on video_intake — anything else is dropped.
  var COLS = ['source', 'name', 'email', 'creator_type', 'video_types',
    'raw_footage_length', 'finished_video_length', 'editing_process',
    'monthly_editing_expense', 'beta_testing', 'message', 'notes'];

  window.stromationIntake = async function (row) {
    var clean = {};
    COLS.forEach(function (k) {
      if (row[k] !== undefined && row[k] !== null && row[k] !== '') clean[k] = row[k];
    });
    if (!clean.email) throw new Error('email required');
    var resp = await fetch(SB_URL + '/rest/v1/video_intake', {
      method: 'POST',
      headers: {
        'apikey': SB_KEY,
        'Authorization': 'Bearer ' + SB_KEY,
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
      },
      body: JSON.stringify(clean)
    });
    if (!resp.ok) throw new Error('intake insert failed: ' + resp.status);
    return true;
  };
})();
