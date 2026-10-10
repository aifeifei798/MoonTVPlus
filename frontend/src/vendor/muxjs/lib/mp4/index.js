/**
 * mux.js
 *
 * Copyright (c) Brightcove
 * Licensed Apache-2.0 https://github.com/videojs/mux.js/blob/master/LICENSE
 */
module.exports = {
  generator: require('./mp4-generator.js'),
  probe: require('./probe.js'),
  Transmuxer: require('./transmuxer.js').Transmuxer,
  AudioSegmentStream: require('./transmuxer.js').AudioSegmentStream,
  VideoSegmentStream: require('./transmuxer.js').VideoSegmentStream,
  CaptionParser: require('./caption-parser.js')
};
