/**
 * mux.js
 *
 * Copyright (c) Brightcove
 * Licensed Apache-2.0 https://github.com/videojs/mux.js/blob/master/LICENSE
 */
module.exports = {
  tag: require('./flv-tag.js'),
  Transmuxer: require('./transmuxer.js'),
  getFlvHeader: require('./flv-header.js')
};
