/* Print currently-open toplevel windows as JSON: [{x,y,w,h,app_id,title}, ...]
 * in global logical-pixel space (matching GTK monitor geometry / the
 * full-desktop screenshot). COSMIC-only: uses zcosmic_toplevel_info_v1
 * (for geometry) correlated with ext_foreign_toplevel_list_v1 (to enumerate).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include "ext-foreign-toplevel-list-v1-client.h"
#include "cosmic-toplevel-info-unstable-v1-client.h"

#define MAX_WIN 256
#define MAX_OUT 16

struct out_pos { struct wl_output *o; int x, y; };
struct win {
    struct ext_foreign_toplevel_handle_v1 *ext;
    struct zcosmic_toplevel_handle_v1 *cos;
    struct wl_output *geo_output;
    int x, y, w, h, have_geo;
    int minimized, active;
    char app_id[256], title[512];
};

static struct ext_foreign_toplevel_list_v1 *g_list;
static struct zcosmic_toplevel_info_v1 *g_info;
static struct out_pos g_outs[MAX_OUT]; static int g_nout;
static struct win g_wins[MAX_WIN]; static int g_nwin;

static int out_x(struct wl_output *o){for(int i=0;i<g_nout;i++)if(g_outs[i].o==o)return g_outs[i].x;return 0;}
static int out_y(struct wl_output *o){for(int i=0;i<g_nout;i++)if(g_outs[i].o==o)return g_outs[i].y;return 0;}

/* --- wl_output: track global position --- */
static void out_geometry(void *d, struct wl_output *o, int32_t x, int32_t y,
        int32_t pw, int32_t ph, int32_t sub, const char *make, const char *model, int32_t tr){
    (void)d;(void)pw;(void)ph;(void)sub;(void)make;(void)model;(void)tr;
    for(int i=0;i<g_nout;i++) if(g_outs[i].o==o){g_outs[i].x=x;g_outs[i].y=y;return;}
    if(g_nout<MAX_OUT){g_outs[g_nout].o=o;g_outs[g_nout].x=x;g_outs[g_nout].y=y;g_nout++;}
}
static void out_mode(void*d,struct wl_output*o,uint32_t f,int32_t w,int32_t h,int32_t r){(void)d;(void)o;(void)f;(void)w;(void)h;(void)r;}
static void out_done(void*d,struct wl_output*o){(void)d;(void)o;}
static void out_scale(void*d,struct wl_output*o,int32_t s){(void)d;(void)o;(void)s;}
static void out_name(void*d,struct wl_output*o,const char*n){(void)d;(void)o;(void)n;}
static void out_desc(void*d,struct wl_output*o,const char*n){(void)d;(void)o;(void)n;}
static const struct wl_output_listener out_listener={out_geometry,out_mode,out_done,out_scale,out_name,out_desc};

/* --- cosmic toplevel handle: geometry --- */
static struct win *win_for_cos(struct zcosmic_toplevel_handle_v1 *c){
    for(int i=0;i<g_nwin;i++) if(g_wins[i].cos==c) return &g_wins[i];
    return NULL;
}
static void cos_geometry(void *d, struct zcosmic_toplevel_handle_v1 *c,
        struct wl_output *output, int32_t x, int32_t y, int32_t w, int32_t h){
    (void)d; struct win *win=win_for_cos(c); if(!win) return;
    if(!win->have_geo || (w*h > win->w*win->h)){ /* prefer the larger geometry */
        win->geo_output=output; win->x=x; win->y=y; win->w=w; win->h=h; win->have_geo=1;
    }
}
static void cos_closed(void*d,struct zcosmic_toplevel_handle_v1*c){(void)d;struct win*w=win_for_cos(c);if(w)w->have_geo=0;}
static void cos_done(void*d,struct zcosmic_toplevel_handle_v1*c){(void)d;(void)c;}
static void cos_title(void*d,struct zcosmic_toplevel_handle_v1*c,const char*t){(void)d;(void)c;(void)t;}
static void cos_app_id(void*d,struct zcosmic_toplevel_handle_v1*c,const char*a){(void)d;(void)c;(void)a;}
static void cos_output_enter(void*d,struct zcosmic_toplevel_handle_v1*c,struct wl_output*o){(void)d;(void)c;(void)o;}
static void cos_output_leave(void*d,struct zcosmic_toplevel_handle_v1*c,struct wl_output*o){(void)d;(void)c;(void)o;}
static void cos_state(void*d,struct zcosmic_toplevel_handle_v1*c,struct wl_array*s){
    (void)d; struct win*win=win_for_cos(c); if(!win) return;
    win->minimized=0; win->active=0;
    uint32_t *v;
    for(v=s->data; (char*)v < (char*)s->data + s->size; v++){
        if(*v==1) win->minimized=1;      /* state enum: 1=minimized, 2=activated */
        else if(*v==2) win->active=1;
    }
}
/* Workspace events are unused but MUST be present so event opcodes line up with
 * the v3 protocol (geometry is opcode 9, after the workspace events). */
struct zcosmic_workspace_handle_v1; struct ext_workspace_handle_v1;
static void cos_ws_enter(void*d,struct zcosmic_toplevel_handle_v1*c,struct zcosmic_workspace_handle_v1*w){(void)d;(void)c;(void)w;}
static void cos_ws_leave(void*d,struct zcosmic_toplevel_handle_v1*c,struct zcosmic_workspace_handle_v1*w){(void)d;(void)c;(void)w;}
static void cos_extws_enter(void*d,struct zcosmic_toplevel_handle_v1*c,struct ext_workspace_handle_v1*w){(void)d;(void)c;(void)w;}
static void cos_extws_leave(void*d,struct zcosmic_toplevel_handle_v1*c,struct ext_workspace_handle_v1*w){(void)d;(void)c;(void)w;}
static const struct zcosmic_toplevel_handle_v1_listener cos_listener={
    cos_closed,cos_done,cos_title,cos_app_id,cos_output_enter,cos_output_leave,
    cos_ws_enter,cos_ws_leave,cos_state,cos_geometry,cos_extws_enter,cos_extws_leave};

/* --- ext foreign toplevel handle: title/app_id --- */
static struct win *win_for_ext(struct ext_foreign_toplevel_handle_v1 *e){
    for(int i=0;i<g_nwin;i++) if(g_wins[i].ext==e) return &g_wins[i];
    return NULL;
}
static void ext_closed(void*d,struct ext_foreign_toplevel_handle_v1*e){
    (void)d;struct win*w=win_for_ext(e);if(w)w->have_geo=0;}
static void ext_done(void*d,struct ext_foreign_toplevel_handle_v1*e){(void)d;(void)e;}
static void ext_title(void*d,struct ext_foreign_toplevel_handle_v1*e,const char*t){
    (void)d;struct win*w=win_for_ext(e);if(w){strncpy(w->title,t?t:"",sizeof w->title-1);}}
static void ext_app_id(void*d,struct ext_foreign_toplevel_handle_v1*e,const char*a){
    (void)d;struct win*w=win_for_ext(e);if(w){strncpy(w->app_id,a?a:"",sizeof w->app_id-1);}}
static void ext_identifier(void*d,struct ext_foreign_toplevel_handle_v1*e,const char*i){(void)d;(void)e;(void)i;}
static const struct ext_foreign_toplevel_handle_v1_listener ext_handle_listener={
    ext_closed,ext_done,ext_title,ext_app_id,ext_identifier};

/* --- ext list: new toplevel --- */
static void list_toplevel(void*d,struct ext_foreign_toplevel_list_v1*l,
        struct ext_foreign_toplevel_handle_v1*e){
    (void)d;(void)l;
    if(g_nwin>=MAX_WIN) return;
    struct win *w=&g_wins[g_nwin++];
    memset(w,0,sizeof *w); w->ext=e;
    ext_foreign_toplevel_handle_v1_add_listener(e,&ext_handle_listener,NULL);
}
static void list_finished(void*d,struct ext_foreign_toplevel_list_v1*l){(void)d;(void)l;}
static const struct ext_foreign_toplevel_list_v1_listener list_listener={list_toplevel,list_finished};

/* --- registry --- */
static void reg_global(void*d,struct wl_registry*r,uint32_t name,const char*iface,uint32_t ver){
    (void)d;
    if(!strcmp(iface,ext_foreign_toplevel_list_v1_interface.name)){
        g_list=wl_registry_bind(r,name,&ext_foreign_toplevel_list_v1_interface,1);
        ext_foreign_toplevel_list_v1_add_listener(g_list,&list_listener,NULL);
    }
    else if(!strcmp(iface,zcosmic_toplevel_info_v1_interface.name))
        g_info=wl_registry_bind(r,name,&zcosmic_toplevel_info_v1_interface, ver<2?ver:2);
    else if(!strcmp(iface,wl_output_interface.name)){
        struct wl_output*o=wl_registry_bind(r,name,&wl_output_interface,2);
        wl_output_add_listener(o,&out_listener,NULL);
    }
}
static void reg_remove(void*d,struct wl_registry*r,uint32_t n){(void)d;(void)r;(void)n;}
static const struct wl_registry_listener reg_listener={reg_global,reg_remove};

static void jputs(const char*s){
    putchar('"');
    for(;*s;s++){ if(*s=='"'||*s=='\\'){putchar('\\');putchar(*s);} else if((unsigned char)*s>=0x20) putchar(*s); }
    putchar('"');
}

int main(void){
    struct wl_display *dpy=wl_display_connect(NULL);
    if(!dpy){fprintf(stderr,"no wayland display\n");return 2;}
    struct wl_registry *reg=wl_display_get_registry(dpy);
    wl_registry_add_listener(reg,&reg_listener,NULL);
    wl_display_roundtrip(dpy);                 /* bind globals + outputs */
    if(!g_list||!g_info){fprintf(stderr,"cosmic toplevel protocols unavailable\n");return 3;}
    wl_display_roundtrip(dpy);                 /* output geometry + toplevel list */
    /* Now that g_info is bound and the toplevels are known, request a cosmic
     * handle for each so geometry events get delivered. */
    for(int i=0;i<g_nwin;i++){
        g_wins[i].cos=zcosmic_toplevel_info_v1_get_cosmic_toplevel(g_info,g_wins[i].ext);
        zcosmic_toplevel_handle_v1_add_listener(g_wins[i].cos,&cos_listener,NULL);
    }
    /* Geometry events may trickle in; pump for up to ~500ms or until every
     * toplevel has a geometry. */
    for(int tries=0; tries<10; tries++){
        wl_display_roundtrip(dpy);
        int missing=0;
        for(int i=0;i<g_nwin;i++) if(!g_wins[i].have_geo) missing++;
        if(!missing) break;
        usleep(50*1000);
    }

    if(getenv("WINLIST_DEBUG")){
        fprintf(stderr,"DEBUG list=%p info=%p nwin=%d nout=%d\n",
                (void*)g_list,(void*)g_info,g_nwin,g_nout);
        for(int i=0;i<g_nwin;i++)
            fprintf(stderr,"  win[%d] geo=%d %d,%d %dx%d app=%s title=%s\n",
                i,g_wins[i].have_geo,g_wins[i].x,g_wins[i].y,g_wins[i].w,g_wins[i].h,
                g_wins[i].app_id,g_wins[i].title);
    }
    printf("[");
    int first=1;
    for(int i=0;i<g_nwin;i++){
        struct win*w=&g_wins[i];
        if(!w->have_geo||w->w<=0||w->h<=0) continue;
        int gx=w->x+out_x(w->geo_output), gy=w->y+out_y(w->geo_output);
        if(!first) printf(",");
        first=0;
        printf("{\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d,\"minimized\":%s,\"active\":%s,\"app_id\":",
               gx,gy,w->w,w->h, w->minimized?"true":"false", w->active?"true":"false");
        jputs(w->app_id); printf(",\"title\":"); jputs(w->title); printf("}");
    }
    printf("]\n");
    return 0;
}
