$(document).ready(function(){
    // History replay forms carry hidden dates; only enhance the search form's date inputs.
    $('#library-search input[name="after"], #library-search input[name="before"]').not('[type="hidden"]').each(function () {
        const picker = new jdPicker(this, {
            month_names: ['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'],
            short_day_names: ['日','一','二','三','四','五','六'],
            date_format: 'YYYY-mm-dd',
            show: function () {
                // Keep the hidden popup in the viewport before measuring it.
                // Showing it at its old absolute position can expand a mobile
                // layout viewport and move the input during the opening tap.
                this.rootLayers.css({position: 'fixed', left: 8, top: 8});
                // Measure the final size, not the zero-width start of slideDown.
                this.rootLayers.stop(true, true).show();
                jdPicker.prototype.show.call(this);
            },
            insideSelector: function (event) { return this.dateSelector[0].contains(event.target); },
            setPosition: function () {
                const input = this.input[0].getBoundingClientRect();
                const width = this.dateSelector.outerWidth();
                const height = this.dateSelector[0].scrollHeight;
                const left = Math.max(8, Math.min(input.left, document.documentElement.clientWidth - width - 8));
                const top = input.bottom + height + 8 > window.innerHeight && input.top > height + 8
                    ? input.top - height - 6 : input.bottom + 6;
                this.rootLayers.css({position: 'fixed', left: left, top: top,
                    maxHeight: Math.max(100, window.innerHeight - top - 8), overflow: 'auto'});
            }
        });
        const reposition = () => { if (picker.dateSelector.is(':visible')) picker.setPosition(); };
        window.addEventListener('resize', reposition);
        window.addEventListener('scroll', reposition, true);
    });
});

function addOpenSearch() {
  if (window.external && ("AddSearchProvider" in window.external)) {
    var link = document.querySelector('link[rel="search"]');
    if (link) window.external.AddSearchProvider(link.href);
  } else {
    alert("此浏览器不支持自动添加搜索引擎，请在浏览器设置中手动添加。");
  }
}
