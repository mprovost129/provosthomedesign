// Select2 initialization for client field in project form
$(document).ready(function() {
    if ($('.select2').length) {
        $('.select2').select2({
            width: '100%',
            placeholder: 'Select a client',
            allowClear: true
        });
    }
});
