(function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("csv-file");
  const form = document.getElementById("upload-form");
  if (!dropzone || !fileInput || !form) return;

  fileInput.addEventListener("change", function () {
    if (fileInput.files.length) form.submit();
  });

  ["dragenter", "dragover"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", function (e) {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) {
      fileInput.files = files;
      form.submit();
    }
  });
})();
