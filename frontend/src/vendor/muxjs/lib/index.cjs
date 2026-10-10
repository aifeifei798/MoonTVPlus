/**
 * mux.js
 *
 * Copyright (c) Brightcove
 * Licensed Apache-2.0 https://github.com/videojs/mux.js/blob/master/LICENSE
 *
 * Vendored from the `Momo707577045/mux.js` fork MoonTV installed as
 * `github:Momo707577045/mux.js`, pruned to its `files` field. Two deliberate
 * deviations from the clone, both needed because rollup bundles this tree
 * instead of Node loading it:
 *
 *   * this entry is `index.cjs`, not `index.js`: rollup only gives a CommonJS
 *     module a `default` export, which is what `import muxjs from 'mux.js'` in
 *     `src/lib/mp4-transmuxer.ts` needs.
 *   * every `require()` in the tree names an exact file (`./codecs/index.js`,
 *     `../utils/clock.js`). Node would find those through its extension search
 *     for `require('./codecs')` and `require('../utils/clock')`; the CommonJS
 *     transform that bundles them resolves the path as written.
 *   * the tree is listed in `build.commonjsOptions.include` in the Vite config
 *     (its default is `/node_modules/` only). Without that, the transform never
 *     runs here and every `require()` survives into the ESM output as a literal
 *     call, which throws `ReferenceError: require is not defined` at import
 *     time — the module never evaluates, so the document stays blank.
 */
'use strict';

var muxjs = {
  codecs: require('./codecs/index.js'),
  mp4: require('./mp4/index.js'),
  flv: require('./flv/index.js'),
  mp2t: require('./m2ts/index.js'),
  partial: require('./partial/index.js')
};

// include all the tools when the full library is required
muxjs.mp4.tools = require('./tools/mp4-inspector.js');
muxjs.flv.tools = require('./tools/flv-inspector.js');
muxjs.mp2t.tools = require('./tools/ts-inspector.js');


module.exports = muxjs;
