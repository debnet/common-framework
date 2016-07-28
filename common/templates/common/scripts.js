
(function($){
    "use strict";

    function DjangoJsError(message) {
        this.name = "DjangoJsError";
        this.message = (message || "");
    }
    DjangoJsError.prototype = new Error();
    DjangoJsError.prototype.constructor = DjangoJsError;

    var Django = window.Django = {

        token_regex: /:\w*:/g,
        named_token_regex: /:(\w+):/g,

        /**
         * Initialize required attributes
         */
        initialize: function() {
            this.urls = JSON.parse("{{ urls|safe|escapejs }}");
            this.context = JSON.parse("{{ context|safe|escapejs }}");
            this.user = JSON.parse("{{ user|safe|escapejs }}");
            this.user.has_perm = function(permission) {
                if (this.is_superuser) return true;
                return this.permissions.indexOf(permission) > -1;
            };
        },

        /**
         * Equivalent to ``reverse`` function and ``url`` template tag.
         */
        url: function(name, args) {
            var pattern = this.urls[name] || false,
                url = pattern,
                key, regex, token, parts;

            if (!url) {
                throw new DjangoJsError('URL for view "' + name + '" not found');
            }

            if (args === undefined) {
                return url;
            }

            if ($.isArray(args)) {
                return this._url_from_array(name, pattern, args);
            }
            else if ($.isPlainObject(args)) {
                return this._url_from_object(name, pattern, args);
            }
            else {
                var argsArray = Array.prototype.slice.apply(arguments, [1, arguments.length]);
                return this._url_from_array(name, pattern, argsArray);
            }
        },

        _url_from_array: function(name, pattern, array) {
            var matches = pattern.match(this.token_regex),
                parts = pattern.split(this.token_regex),
                url = parts[0];

            if (!matches && array.length === 0) {
                return url;
            }

            if (matches && matches.length != array.length) {
                throw new DjangoJsError('Wrong number of argument for pattern "' + name + '"');
            }


            for (var idx=0; idx < array.length; idx++) {
                url += array[idx] + parts[idx + 1];
            }

            return url;
        },

        _url_from_object: function(name, pattern, object) {
            var url = pattern,
                tokens = pattern.match(this.token_regex);

            if (!tokens) {
                return url;
            }

            for (var idx=0; idx < tokens.length; idx++) {
                var token = tokens[idx],
                    prop = token.slice(1, -1),
                    value = object[prop];

                if (value === undefined) {
                    throw new DjangoJsError('Property "' + prop + '" not found');
                }

                url = url.replace(token, value);
            }

            return url;
        },

        /**
         * Equivalent to ``static`` template tag.
         */
        file: function(filename) {
            return this.context.STATIC_URL + filename;
        },

        /**
         * Equivalent to ``static`` template tag.
         */
        'static': function(filename) {
            return this.context.STATIC_URL + filename;
        },

        /**
         * Return cookie value by name.
         *  cf. https://docs.djangoproject.com/en/dev/ref/contrib/csrf/#ajax
         */
        _getCookie: function(name) {
            var cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                var cookies = document.cookie.split(';');
                for (var i = 0; i < cookies.length; i++) {
                    var cookie = $.trim(cookies[i]);
                    // Does this cookie string begin with the name we want?
                    if (cookie.substring(0, name.length + 1) == (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        },

        /**
         * Get the CSRF token from the cookie.
         */
        csrf_token: function() {
            return this._getCookie('csrftoken');
        },

        /**
         * Equivalent to ``csrf_token`` template tag.
         */
        csrf_element: function() {
            var token = this.csrf_token(),
                elem = [
                '<input type="hidden" name="csrfmiddlewaretoken" value="',
                token ? token : '',
                '">'
            ];

            return elem.join('');
        },

        /**
         *  Fix ajax request with CSRF Django middleware.
         */
        jquery_csrf: function() {
            var getCookie = this._getCookie;
            $(document).ajaxSend(function(event, xhr, settings) {
                function sameOrigin(url) {
                    // url could be relative or scheme relative or absolute
                    var host = document.location.host; // host + port
                    var protocol = document.location.protocol;
                    var sr_origin = '//' + host;
                    var origin = protocol + sr_origin;
                    // Allow absolute or scheme relative URLs to same origin
                    return (url == origin || url.slice(0, origin.length + 1) == origin + '/') || (url == sr_origin || url.slice(0, sr_origin.length + 1) == sr_origin + '/') ||
                    // or any other URL that isn't scheme relative or absolute i.e relative.
                    !(/^(\/\/|http:|https:).*/.test(url));
                }

                function safeMethod(method) {
                    return (/^(GET|HEAD|OPTIONS|TRACE)$/.test(method));
                }

                if (!safeMethod(settings.type) && sameOrigin(settings.url)) {
                    xhr.setRequestHeader("X-CSRFToken", getCookie('csrftoken'));
                }
            });
        },
    };

    Django.initialize();
    Django.jquery_csrf();
    return Django;

}(window.jQuery));
